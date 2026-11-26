"""Default roles (Django groups), each with a starting set of permissions.

Runs once per database. A role that already exists is left alone, so admins
can rename, edit or delete these roles and nothing puts them back. Staff
users bypass permissions, so there is no admin role here.
"""

from django.contrib.auth.management import create_permissions
from django.db import migrations
from django.db.models import Q

VIEW = ['view']
ADD_VIEW = ['add', 'view']
EDIT = ['add', 'change', 'view']
FULL = ['add', 'change', 'delete', 'view']

# role name -> {"app.model": [actions]}
DEFAULT_ROLES = {
    'Branch Manager': {
        'lead.lead': FULL,
        'lead.leadcall': FULL,
        'lead.followup': FULL,
        'lead.leadactivity': FULL,
        'lead.leaddocument': FULL,
        'lead.leadcourseinterest': FULL,
        'lead.voicenote': FULL,
        'lead.enrollment': EDIT,
        'lead.payment': EDIT,
        'lead.installment': EDIT,
        'lead.paymentplan': EDIT,
        'lead.adjustment': ADD_VIEW,
        'lead.teamslicence': EDIT,
        'lead.leadsource': EDIT,
        'lead.campaign': EDIT,
        'student.student': EDIT,
        'student.studentdocument': EDIT,
        'student.attendance': EDIT,
        'course.course': VIEW,
        'course.coursebatch': EDIT,
        'course.batch': EDIT,
        'course.batchcost': EDIT,
        'invoices.invoice': EDIT,
        'account.user': VIEW,
        'branch.branch': VIEW,
    },
    'Counselor': {
        'lead.lead': EDIT,
        'lead.leadcall': EDIT,
        'lead.followup': EDIT,
        'lead.leadactivity': ADD_VIEW,
        'lead.leaddocument': ADD_VIEW,
        'lead.leadcourseinterest': EDIT,
        'lead.voicenote': ADD_VIEW,
        'lead.enrollment': ADD_VIEW,
        'lead.leadsource': ADD_VIEW,
        'lead.campaign': ADD_VIEW,
        'student.student': VIEW,
        'course.course': VIEW,
        'course.coursebatch': VIEW,
    },
    'Accountant': {
        'lead.lead': VIEW,
        'lead.enrollment': ['change', 'view'],
        'lead.payment': EDIT,
        'lead.installment': EDIT,
        'lead.paymentplan': EDIT,
        'lead.adjustment': ADD_VIEW,
        'invoices.invoice': EDIT,
        'student.student': VIEW,
        'course.course': VIEW,
        'course.coursebatch': VIEW,
        'course.batchcost': EDIT,
        'course.batchclosing': VIEW,
    },
    'Front Desk': {
        'lead.lead': EDIT,
        'lead.leadcall': ADD_VIEW,
        'lead.followup': ADD_VIEW,
        'lead.leadactivity': ADD_VIEW,
        'lead.leadcourseinterest': ADD_VIEW,
        'lead.leadsource': VIEW,
        'student.student': EDIT,
        'student.attendance': EDIT,
        'course.course': VIEW,
        'course.coursebatch': VIEW,
    },
    'Trainer': {
        'student.student': VIEW,
        'student.attendance': EDIT,
        'course.course': VIEW,
        'course.coursebatch': VIEW,
        'course.batch': VIEW,
    },
}


def add_default_roles(apps, schema_editor):
    # Permissions are normally made after migrate; make them now so the roles
    # can use them on a fresh database.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    for name, models in DEFAULT_ROLES.items():
        group, created = Group.objects.get_or_create(name=name)
        if not created:
            continue
        wanted = Q(pk__in=[])
        for label, actions in models.items():
            app_label, model = label.split('.')
            wanted |= Q(
                content_type__app_label=app_label,
                codename__in=[f'{action}_{model}' for action in actions],
            )
        group.permissions.set(Permission.objects.filter(wanted))


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('account', '0009_rename_balance_to_seconds'),
        ('branch', '0001_initial'),
        ('course', '0012_batch_close_checklist'),
        ('invoices', '0006_protect_money_records'),
        ('lead', '0038_remove_lead_type'),
        ('student', '0004_backfill_lead_conversion'),
    ]

    operations = [
        migrations.RunPython(add_default_roles, migrations.RunPython.noop),
    ]
