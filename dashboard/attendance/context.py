from .models import Notification


def unread_notifications(request):
    if request.user.is_authenticated:
        return {"unread_count": Notification.objects.filter(is_read=False).count()}
    return {}
