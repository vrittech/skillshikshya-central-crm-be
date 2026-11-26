from django.db.models import Q
from rest_framework.filters import BaseFilterBackend


def walled_branch_id(user):
    """The branch a user is kept inside, or None when they may see every branch.

    Staff see every branch. Everyone else has a branch: a user without one
    can't sign in (apps.account.authentication).
    """
    if user.is_staff or user.is_superuser:
        return None
    return getattr(user, 'branch_id', None)


class BranchFilterBackend(BaseFilterBackend):
    """Branch users see their branch's rows; staff see all or pick one with ?branch_id.

    A view with `shared_when_no_branch = True` also shows branch users the
    rows that have no branch (shared by every branch, e.g. shifts).
    """

    def filter_queryset(self, request, queryset, view):
        branch_field = getattr(view, 'branch_field', 'branch')
        user_branch = getattr(request.user, 'branch', None)

        if request.user.is_staff or request.user.is_superuser:
            branch_id = request.query_params.get('branch_id')
            if branch_id:
                filter_kwargs = {f'{branch_field}_id': branch_id}
                return queryset.filter(**filter_kwargs)
            return queryset

        if user_branch:
            mine = Q(**{branch_field: user_branch})
            if getattr(view, 'shared_when_no_branch', False):
                mine |= Q(**{f'{branch_field}__isnull': True})
            return queryset.filter(mine)

        return queryset


class LeadBranchFilterBackend(BranchFilterBackend):
    """Leads: a branch sees its own leads, leads with an enquiry taken at the
    branch, and leads with an enrollment at the branch.

    A person may ask at any branch, so one lead can have enquiries from
    several branches. A student moved to another branch takes their open
    enrollments along, and the new branch needs the lead to work them.
    """

    def filter_queryset(self, request, queryset, view):
        user = request.user
        user_branch = getattr(user, 'branch', None)
        if user.is_staff or user.is_superuser:
            branch_id = request.query_params.get('branch_id')
            if branch_id:
                return queryset.filter(
                    Q(branch_id=branch_id)
                    | Q(course_interests__branch_id=branch_id)
                    | Q(enrollments__branch_id=branch_id)
                ).distinct()
            return queryset
        if user_branch:
            return queryset.filter(
                Q(branch=user_branch)
                | Q(course_interests__branch=user_branch)
                | Q(enrollments__branch=user_branch)
            ).distinct()
        return queryset

