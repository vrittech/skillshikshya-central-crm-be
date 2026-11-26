from rest_framework import serializers
from apps.account.models import User


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    user_permissions = serializers.SerializerMethodField()
    branch_name = serializers.CharField(source='branch.name', read_only=True)

    class Meta:
        model = User
        fields = '__all__'
        read_only_fields = ['is_staff', 'is_superuser', 'user_permissions', 'last_login', 'created_at', 'updated_at', 'call_seconds_balance']

    def get_user_permissions(self, obj):
        return sorted(obj.get_all_permissions())

    def validate(self, attrs):
        attrs = self._check_rights(attrs)
        # Everyone but staff works inside one branch.
        target = self.instance
        if not (target is not None and (target.is_staff or target.is_superuser)):
            branch = attrs['branch'] if 'branch' in attrs else getattr(target, 'branch', None)
            if branch is None:
                raise serializers.ValidationError({'branch': ['Pick a branch.']})
        return attrs

    def _check_rights(self, attrs):
        """Who may give which rights.

        Staff may do anything, except that only a superuser edits a superuser.
        Other users with add/change user rights (e.g. a branch manager):
        - can't edit staff, or change their own roles, branch or active state;
        - can't give a role with a right they don't have themselves;
        - keep the users they add or edit inside their own branch.
        """
        request = self.context.get('request')
        actor = getattr(request, 'user', None)
        target = self.instance
        if self.instance is None and not attrs.get('password'):
            raise serializers.ValidationError({'password': ['Set a password for the new user.']})
        if actor is None or not actor.is_authenticated:
            return attrs
        if target is not None and target.is_superuser and not actor.is_superuser:
            raise serializers.ValidationError('Only a superuser can change a superuser.')
        if actor.is_staff or actor.is_superuser:
            return attrs

        if target is not None and target.is_staff:
            raise serializers.ValidationError('Only staff can change a staff user.')
        if target is not None and target.pk == actor.pk:
            for field in ('groups', 'branch', 'is_active'):
                if field in attrs:
                    raise serializers.ValidationError(
                        {field: ['You can’t change your own roles, branch or active state.']}
                    )
        mine = actor.get_all_permissions()
        for group in attrs.get('groups') or []:
            rights = {
                f'{app}.{code}'
                for app, code in group.permissions.values_list('content_type__app_label', 'codename')
            }
            if not rights <= mine:
                raise serializers.ValidationError(
                    {'groups': [f'You can’t give the {group.name} role: it has rights you don’t have.']}
                )
        if actor.branch_id is not None:
            if target is not None and target.branch_id != actor.branch_id:
                raise serializers.ValidationError('You can only change users of your own branch.')
            if 'branch' in attrs and attrs['branch'] != actor.branch:
                raise serializers.ValidationError({'branch': ['Pick your own branch.']})
            if target is None:
                attrs['branch'] = actor.branch
        return attrs

    def create(self, validated_data):
        groups = validated_data.pop('groups', [])
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        if groups:
            user.groups.set(groups)
        return user

    def update(self, instance, validated_data):
        groups = validated_data.pop('groups', None)
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if groups is not None:
            instance.groups.set(groups)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class UserMiniSerializer(serializers.ModelSerializer):
    user_permissions = serializers.SerializerMethodField()
    branch_name = serializers.CharField(source='branch.name', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'name', 'email', 'is_staff', 'is_superuser', 'branch', 'branch_name', 'groups', 'user_permissions']

    def get_user_permissions(self, obj):
        return sorted(obj.get_all_permissions())
