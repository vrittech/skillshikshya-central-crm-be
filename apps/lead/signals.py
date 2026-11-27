from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.db.models import ProtectedError

from apps.lead.models import Lead, LeadSource, Notification


@receiver(pre_delete, sender=LeadSource)
def block_restricted_source_deletion(sender, instance, **kwargs):
    """
    Model-level backstop: protected or in-use sources cannot be deleted,
    even from Django Admin or bulk queryset deletes.
    """
    if instance.is_protected:
        raise ProtectedError(
            'This lead source is protected and cannot be deleted.',
            [instance],
        )
    if Lead.objects.filter(source=instance).exists():
        raise ProtectedError(
            "This source is used in leads so it cannot be deleted.",
            [instance],
        )


@receiver(post_save, sender=Lead)
def notify_counselor_assignment(sender, instance, created, **kwargs):
    """Tell the counselor a lead is theirs: a notification in the CRM and an email.

    Also when the lead is created with a counselor. Not when the counselor
    assigned the lead to themselves.
    """
    from apps.lead.emails import lead_assigned

    if getattr(instance, '_skip_assign_notice', False):
        return  # An import tells each counselor once, for all their leads.
    new_id = instance.assigned_counselor_id
    if not new_id or (not created and instance._original_assigned_counselor_id == new_id):
        return
    user = instance._changed_by or (instance.created_by if created else None)
    if user is not None and user.pk == new_id:
        return
    Notification.objects.create(
        recipient=instance.assigned_counselor,
        title='New Lead Assigned',
        message=f'Lead {instance.reference_number} ({instance.full_name}) has been assigned to you.',
        link=f'/leads/{instance.pk}',
    )
    lead_assigned(instance, user=user)
