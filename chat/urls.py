
from django.urls import path , include
from .views import *

urlpatterns = [
    path('', index),
    path('signin/', SignInView.as_view()),
	path('signup/', SignUpView.as_view()),

]
