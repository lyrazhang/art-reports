from datetime import datetime

from rest_framework import serializers
from rest_framework.authtoken.models import Token

from benchmarks import models as benchmarks_models
from jobs import models as jobs_models



class DynamicFieldsMixin(object):
    """
    A serializer mixin that takes an additional `fields` argument that controls
    which fields should be displayed.
    Usage::
        class MySerializer(DynamicFieldsMixin, serializers.HyperlinkedModelSerializer):
            class Meta:
                model = MyModel
    """
    def __init__(self, *args, **kwargs):
        super(DynamicFieldsMixin, self).__init__(*args, **kwargs)
        fields = self.context['request'].query_params.get('fields')
        if fields:
            fields = fields.split(',')
            # Drop any fields that are not specified in the `fields` argument.
            allowed = set(fields)
            existing = set(self.fields.keys())
            for field_name in existing - allowed:
                self.fields.pop(field_name)


class BoardSerializer(serializers.ModelSerializer):
    class Meta:
        model = benchmarks_models.Board

    def create(self, validated_data):
        board, created = benchmarks_models.Board.objects.get_or_create(**validated_data)
        return board


class BenchmarkSerializer(serializers.ModelSerializer):
    class Meta:
        model = benchmarks_models.Benchmark

    def create(self, validated_data):
        benchmark, created = benchmarks_models.Benchmark.objects.get_or_create(**validated_data)
        return benchmark


class ReducedManifestSerializer(DynamicFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = benchmarks_models.Manifest
        fields = ("id", "manifest_hash", "results")
        depth = 2


class ManifestSerializer(DynamicFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = benchmarks_models.Manifest

    def create(self, validated_data):
        manifest, _ = benchmarks_models.Manifest.objects.get_or_create(**validated_data)
        return manifest


class ResultDataSerializer(DynamicFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = benchmarks_models.ResultData

    def create(self, validated_data):
        defaults = {
            "created_at": datetime.now()
        }
        resultdata, created = benchmarks_models.ResultData.objects.get_or_create(defaults=defaults, **validated_data)
        return resultdata

class ResultSerializer(DynamicFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = benchmarks_models.Result
        #depth = 1

    def create(self, validated_data):
        defaults = {
            "created_at": datetime.now()
        }
        result, created = benchmarks_models.Result.objects.get_or_create(defaults=defaults, **validated_data)
        return result



class TestJob(serializers.ModelSerializer):
    class Meta:
        model = jobs_models.TestJob


class BuildJob(serializers.ModelSerializer):
    test_jobs = TestJob(many=True, read_only=True)

    class Meta:
        model = jobs_models.BuildJob


class TokenSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username')
    class Meta:
        model = Token
        fields = ('key', 'username')
