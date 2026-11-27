from django.contrib.auth.models import Group, Permission
from rest_framework import serializers


class GroupSerializer(serializers.ModelSerializer):
    permissions = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Permission.objects.all(), required=False
    )

    class Meta:
        model = Group
        fields = ['id', 'name', 'permissions']
        # validate_name checks for duplicates, with a role-worded message.
        extra_kwargs = {'name': {'validators': []}}

    def validate_name(self, value):
        # "counselor" next to "Counselor" would look like the same role.
        taken = Group.objects.filter(name__iexact=value)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError('A role with this name already exists.')
        return value
