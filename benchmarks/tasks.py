import os
import urlparse
import re
import requests
import subprocess
import traceback

from collections import defaultdict
from dateutil.relativedelta import relativedelta
from urllib import urlencode

from django.conf import settings
from django.utils import timezone
from django.core.files.base import ContentFile
from django.template.loader import render_to_string

from celery.utils.log import get_task_logger

from crayonbox import celery_app

from . import models, testminer, mail, gerrit, progress

logger = get_task_logger("tasks")


@celery_app.task(bind=True)
def set_testjob_results(self, testjob_id):
    testjob = models.TestJob.objects.get(pk=testjob_id)
    try:
        test_results = get_testjob_data(testjob)
        store_testjob_data(testjob, test_results)
    except testminer.LavaServerException as ex:
        if ex.status_code / 100 == 5:
            # HTTP 50x (internal server errors): server is too busy, in
            # maintaince, or broken; will try again later
            logger.info(ex.message)
            return
        else:
            raise

def store_testjob_data(testjob, test_results):
    testjob.save()

    if testjob.results_loaded:
        return

    if not test_results:
        return

    summary = defaultdict(lambda: [])

    root_group, _ = models.BenchmarkGroup.objects.get_or_create(name='/')

    for result in test_results:
        if 'benchmark_group' in result:
            benchmark_group, _ = models.BenchmarkGroup.objects.get_or_create(
                name=result['benchmark_group']
            )
        else:
            benchmark_group = None
        benchmark, _ = models.Benchmark.objects.get_or_create(
            name=result['benchmark_name'],
            group=benchmark_group,
        )

        subscore_results = {}
        for item in result['subscore']:
            if item['name'] in subscore_results:
                subscore_results[item['name']].append(item['measurement'])
            else:
                subscore_results[item['name']] = [item['measurement']]

        for name, values in subscore_results.items():
            models.ResultData.objects.create(
                name=name,
                created_at=testjob.created_at,
                values=values,
                result=testjob.result,
                test_job_id=testjob.id,
                benchmark=benchmark
            )
            if benchmark_group:
                for v in values:
                    summary[benchmark_group.id].append(v)
                    summary[root_group.id].append(v)

    for (gid, values) in summary.items():
        group = models.BenchmarkGroup.objects.get(pk=gid)
        models.BenchmarkGroupSummary.objects.create(
            group=group,
            environment=testjob.environment,
            created_at=testjob.created_at,
            result=testjob.result,
            test_job_id=testjob.id,
            values=values,
        )


    testjob.results_loaded = True
    testjob.save()


def get_testjob_data(testjob):

    logger.info("Fetch benchmark results for %s" % testjob)

    netloc = urlparse.urlsplit(testjob.testrunnerurl).netloc
    username, password = settings.CREDENTIALS[netloc]
    tester = getattr(testminer, testjob.testrunnerclass)(
        testjob.testrunnerurl, username, password
    )

    testjob.status = tester.get_test_job_status(testjob.id)
    testjob.url = tester.get_job_url(testjob.id)

    if not testjob.initialized:
        testjob.testrunnerclass = tester.get_result_class_name(testjob.id)
        testjob.initialized = True
        tester = getattr(testminer, testjob.testrunnerclass)(
            testjob.testrunnerurl, username, password
        )

    if testjob.status not in ["Complete", "Incomplete", "Canceled"]:
        logger.debug("Job({0}) status: {1}".format(testjob.id, testjob.status))
        return

    details = tester.get_test_job_details(testjob.id)
    testjob.definition = details['definition']
    testjob.metadata = details['metadata']
    testjob.name = details['name']
    testjob.environment = tester.get_environment(testjob.metadata, models.Environment)
    testjob.completed = True
    logger.debug("Test job({0}) completed: {1}".format(testjob.id, testjob.completed))
    if testjob.status in ["Incomplete", "Canceled"]:
        logger.debug("Job({0}) status: {1}".format(testjob.id, testjob.status))
        return

    logger.debug("Calling testminer")
    logger.debug("Tester class:{0}".format(tester.__class__.__name__))
    logger.debug("Testjob:{0}".format(testjob.id))

    test_results = tester.get_test_job_results(testjob.id)

    if not test_results and testjob.testrunnerclass != "GenericLavaTestSystem":
        testjob.status = "Results Missing"
        return

    datafile_name, datafile_content = tester.get_result_data(testjob.id)

    if datafile_name and datafile_content:
        datafile = ContentFile(datafile_content)
        testjob.data.save(datafile_name, datafile, save=False)

    tester.cleanup()

    # ToDo: not implemented yet. DO NOT REMOVE
    # for result in test_results['test']:
    #    name = result['testdef']
    #    if 'results' in result.keys():
    #        print "\t\tTest(%s): %s" % (name, result['result'])
    #    if 'parameters' in result.keys():
    #        print "\t\t\tParameters: %s" % (result['parameters'])
    #        print result['parameters'].__class__.__name__
    #    if 'results' in result.keys() and result['result'] == 'fail':
    #        print "\t\t\tReason: %s" % (result['reason'])
    #    version = ''
    #    if 'version' in result.keys():
    #        version = result['version']
    #    parameters = {}
    #    if 'parameters' in result.keys():
    #        parameters = result['parameters']

    return test_results


@celery_app.task(bind=True)
def check_testjob_completeness(self):
    incompleted = models.TestJob.objects.filter(completed=False)

    logger.info("Fetch incomplete TestJobs results, count=%s" % incompleted.count())

    for testjob in models.TestJob.objects.filter(completed=False):
        set_testjob_results.apply_async(args=[testjob.id])


@celery_app.task(bind=True)
def update_jenkins(self, result):
    if not settings.UPDATE_JENKINS:
        return

    host = urlparse.urlsplit(result.build_url).netloc

    if host not in settings.CREDENTIALS.keys():
        logger.error("No credentials found for %s" % host)
        return

    key = settings.CREDENTIALS[host][1]

    description = render_to_string("jenkins_update.html", {
        "host": settings.URL,
        "result": result,
        "testjobs": models.TestJob.objects.filter(result=result)
    })

    try:
        jenkins_client_call(
            host,
            key,
            [
                "set-build-description",
                result.name,
                str(result.build_id),
                description,
            ]
        )
    except subprocess.CalledProcessError as error:
        # FIXME the Jenkins CLI tool does not provide any way of checking
        # whether a build exists or not, it will just crash on
        # NullPointerException if the build we are trying to update does not
        # exist.
        if re.match('NullPointerException', error.output) and \
           re.match('hudson.cli.SetBuildDescriptionCommand.run', error.output):
            logger.warning("Exception when updating build description - build not found")
            logger.warning(error.message)
        else:
            logger.error(error.output + "\n" + traceback.format_exc(error))


jenkins_cli_client = os.path.dirname(__file__) + '/jenkins-cli.jar'


def jenkins_client_call(host, key, args):
    cmd = [
        'java', '-jar', jenkins_cli_client,
        '-s', 'https://{0}/jenkins'.format(host),
        '-i', key,
    ] + args

    subprocess.check_output(cmd, stderr=subprocess.STDOUT)


@celery_app.task(bind=True)
def check_result_completeness(self):
    for result in models.Result.objects.filter(reported=False):
        save = False

        if result.completed:
            report_gerrit.apply_async(args=[result])
            result.reported = True
            save = True

        if result.testjobs_updated:
            update_jenkins.apply_async(args=[result])
            save = True

        if save:
            result.save()



@celery_app.task(bind=True)
def report_gerrit(self, current):
    if settings.IGNORE_GERRIT:
        return
    if not current.gerrit_change_id:
        return

    if not current.baseline:
        message = render_to_string("gerrit_update_baseline_missing.html", {
            "current": current,
        })
    elif not current.baseline.data.count():
        message = render_to_string("gerrit_update_baseline_no_results.html", {
            "current": current,
            "baseline": current.baseline
        })
    else:
        message = render_to_string("gerrit_update.html", {
            "current": current,
            "baseline": current.baseline,
        })

    gerrit.update(current, message)


@celery_app.task(bind=True)
def report_email(self, current):
    if not current.gerrit_change_id:
        return

    if not current.baseline:
        return mail.result_progress_baseline_missing(current)

    if not current.data.count():
        return mail.result_progress_no_results(current)

    if not current.baseline.data.count():
        return mail.result_progress_baseline_no_results(current)

    return mail.result_progress(current, current.baseline)


def _sync_external_repos():
    base = settings.EXTERNAL_DIR['BASE']
    for name, address in settings.EXTERNAL_DIR['REPOSITORIES']:

        logger.info("Repository: %s" % address)

        repo_path = os.path.join(base, name)
        if not os.path.exists(repo_path):
            subprocess.check_call(['git', 'clone', address, repo_path], cwd=base)
        else:
            subprocess.check_call(['git', 'pull'], cwd=repo_path)


@celery_app.task(bind=True)
def sync_external_repos(self):
    _sync_external_repos()


@celery_app.task(bind=True)
def daily_benchmark_progress(self):
    now = timezone.now()
    yesterday = now - relativedelta(days=1)

    results = progress.get_progress_since(yesterday)
    if results:
        mail.daily_benchmark_progress(now, yesterday, results)


@celery_app.task(bind=True)
def weekly_benchmark_progress(self):
    now = timezone.now()
    last_week = now - relativedelta(days=7)
    results = progress.get_progress_since(last_week)
    if results:
        mail.weekly_benchmark_progress(now, last_week, results)


@celery_app.task(bind=True)
def monthly_benchmark_progress(self):
    now = timezone.now()
    last_month = now - relativedelta(months=1)

    results = progress.get_progress_since(last_month)
    if results:
        mail.monthly_benchmark_progress(now, last_month, results)
