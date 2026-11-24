from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsStaffOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user and request.user.is_authenticated
        return request.user and request.user.is_staff


class IsStaffOrSelf(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        return obj == request.user


class IsStaffOrHasPermission(BasePermission):
    perms_map = {
        'GET': [],
        'OPTIONS': [],
        'HEAD': [],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }

    def get_required_permissions(self, method, model_cls):
        if method not in self.perms_map:
            raise ValueError(f'Invalid method: {method}')
        return [perm % {
            'app_label': model_cls._meta.app_label,
            'model_name': model_cls._meta.model_name,
        } for perm in self.perms_map[method]]

    def has_permission(self, request, view):
        if request.user.is_staff:
            return True
        if not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        if hasattr(view, 'queryset') and view.queryset is not None:
            model_cls = view.queryset.model
        elif hasattr(view, 'get_queryset'):
            model_cls = view.get_queryset().model
        else:
            return True
        perms = self.get_required_permissions(request.method, model_cls)
        return request.user.has_perms(perms)


def needs(perm):
    """Permission class for one action: staff, or a user with this Django permission."""

    class Needs(BasePermission):
        def has_permission(self, request, view):
            user = request.user
            return bool(user and user.is_authenticated and (user.is_staff or user.has_perm(perm)))

    return Needs
