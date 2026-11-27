"""Students can move to another branch, with a history row each time.

Adds the "move student to another branch" right and gives it to the default
Branch Manager role, if that role is still there.
"""

from django.conf import settings
from django.contrib.auth.management import create_permissions
from django.db import migrations, models
import django.db.models.deletion


# role name -> rights this migration adds to it
ROLE_RIGHTS = {'Branch Manager': ['move_branch']}


def give_role_rights(apps, schema_editor):
    student_app = apps.get_app_config('student')
    student_app.models_module = True
    create_permissions(student_app, apps=apps, verbosity=0)
    student_app.models_module = None
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    for name, codenames in ROLE_RIGHTS.items():
        group = Group.objects.filter(name__iexact=name).first()
        if group is not None:
            group.permissions.add(*Permission.objects.filter(
                content_type__app_label='student', codename__in=codenames,
            ))


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('branch', '0001_initial'),
        ('student', '0004_backfill_lead_conversion'),
        ('account', '0010_default_roles'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='student',
            options={'ordering': ['-created_at'], 'permissions': [('move_branch', 'Can move student to another branch')]},
        ),
        migrations.CreateModel(
            name='BranchMove',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.TextField()),
                ('moved_at', models.DateTimeField(auto_now_add=True)),
                ('from_branch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='branch.branch')),
                ('moved_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='student_branch_moves', to=settings.AUTH_USER_MODEL)),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='branch_moves', to='student.student')),
                ('to_branch', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='branch.branch')),
            ],
            options={
                'db_table': 'student_branch_moves',
                'ordering': ['-moved_at', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='branchmove',
            constraint=models.CheckConstraint(check=models.Q(('reason', ''), _negated=True), name='student_branch_move_reason_required'),
        ),
        migrations.RunPython(give_role_rights, migrations.RunPython.noop),
    ]
