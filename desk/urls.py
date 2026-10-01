from django.urls import path
from desk import views, slack

urlpatterns = [
    path("slack/interactions/", slack.interactions, name="slack"),
    path("slack/access/<str:token>/", views.slack_sign_in, name="slack_login"),
    path("", views.queue, name="queue"),
    path("login/", views.sign_in, name="login"),
    path("logout/", views.sign_out, name="logout"),
    path("join/<str:token>/", views.join, name="join"),
    path("requests/new/", views.new_request, name="new"),
    path("requests/<uuid:pk>/", views.detail, name="detail"),
    path("requests/<uuid:pk>/manage/", views.manage_request, name="manage"),
    path("requests/<uuid:pk>/comment/", views.add_comment, name="comment"),
    path("requests/<uuid:pk>/attachment/", views.add_attachment, name="attachment"),
    path("files/<uuid:pk>/", views.download, name="download"),
    path("team/", views.team, name="team"),
    path("team/<uuid:pk>/revoke/", views.revoke_member, name="revoke"),
    path("settings/", views.settings_page, name="settings"),
    path("health/", views.health, name="health"),
]
