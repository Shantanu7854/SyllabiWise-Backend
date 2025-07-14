from django.urls import path
from .views import PlaylistAnalyzeView

urlpatterns = [
    path('playlist-analyze/', PlaylistAnalyzeView.as_view()),
]
