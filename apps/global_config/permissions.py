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


# Reads -----------------------------------------------------------------
#
# A read (GET, HEAD, OPTIONS) needs a view right, the same way a save needs
# add, change or delete. Branch walls still decide which rows a user sees;
# these rules decide which kinds of record.

OPEN = 'open'    # a pick list every signed-in user's forms need
STAFF = 'staff'  # staff only
NO_READ_MESSAGE = "You don't have permission to see this."


def can(user, right):
    """True when the user holds this right. Staff hold every right."""
    if not (user and user.is_authenticated):
        return False
    if right == OPEN or user.is_staff:
        return True
    if right == STAFF:
        return False
    return user.has_perm(right)


class ReadRights:
    """Mixin for class views: reads need `read_perm`, or `read_perms[action]`.

    read_perm is the right every read of the view needs ('lead.view_payment'),
    OPEN for a pick list or STAFF. read_perms gives one action its own right:
    {'money': 'course.view_batchcost'}. A read with no rule is refused, and a
    test checks that every read address has one.
    """

    read_perm = None
    read_perms = {}

    def read_right(self):
        return self.read_perms.get(getattr(self, 'action', None), self.read_perm)

    def check_permissions(self, request):
        super().check_permissions(request)
        if request.method in SAFE_METHODS:
            right = self.read_right()
            if right is None or not can(request.user, right):
                self.permission_denied(request, message=NO_READ_MESSAGE, code='no_read_right')


def reads(right):
    """Permission class for a function view: its reads need this right (or OPEN)."""

    class Reads(BasePermission):
        read_right = right
        message = NO_READ_MESSAGE

        def has_permission(self, request, view):
            return request.method not in SAFE_METHODS or can(request.user, right)

    return Reads
