from django.urls import path
from .views import PlaylistAnalyzeView, RegisterView  

urlpatterns = [
    path('playlist-analyze/', PlaylistAnalyzeView.as_view()),
    path('register/', RegisterView.as_view()), 
]
