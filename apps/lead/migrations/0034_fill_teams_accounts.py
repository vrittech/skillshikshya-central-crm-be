from django.db import migrations


def fill(apps, schema_editor):
    """Every account already given becomes a pool account, and its licences point to it."""
    TeamsLicence = apps.get_model('lead', 'TeamsLicence')
    TeamsAccount = apps.get_model('lead', 'TeamsAccount')
    for licence in TeamsLicence.objects.order_by('assigned_at', 'id'):
        email = (licence.account or '').strip().lower()
        account, _ = TeamsAccount.objects.get_or_create(
            email=email,
            defaults={'created_by_id': licence.assigned_by_id},  # shared, like the old single total
        )
        TeamsLicence.objects.filter(pk=licence.pk).update(teams_account=account)


class Migration(migrations.Migration):

    dependencies = [
        ('lead', '0033_teams_accounts'),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
