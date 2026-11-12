"""gallery_version: changes whenever embeddings, bans or session membership change (API_CONTRACT 2.1).

The matcher polls /api/sessions/active/ and re-downloads its galleries as soon as this changes.
QuerySet.update() and bulk_create() skip signals: call bump() yourself after any bulk change to members.
"""
from django.db.models import F
from django.db.models.signals import post_delete, post_save

from ..models import AppSettings, Member, Session


def current():
    return str(AppSettings.load().gallery_version)


def bump(**_):
    AppSettings.load()  # make sure the row exists
    AppSettings.objects.filter(pk=1).update(gallery_version=F("gallery_version") + 1)


def connect():
    for model in (Member, Session):
        post_save.connect(bump, sender=model, dispatch_uid=f"gallery-{model.__name__}-save")
        post_delete.connect(bump, sender=model, dispatch_uid=f"gallery-{model.__name__}-delete")
