from django.contrib.auth.models import Permission
from rest_framework import serializers


class PermissionSerializer(serializers.ModelSerializer):
    content_type_name = serializers.SerializerMethodField()

    class Meta:
        model = Permission
        fields = ['id', 'name', 'codename', 'content_type', 'content_type_name']

    def get_content_type_name(self, obj):
        # The section heading on the role screen, e.g. "Lead call".
        model = obj.content_type.model_class()
        name = str(model._meta.verbose_name) if model else obj.content_type.model
        return name[:1].upper() + name[1:]
