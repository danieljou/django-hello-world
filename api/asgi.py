import os
from django.core.asgi import get_asgi_application
from channels.routing  import ProtocolTypeRouter , URLRouter
from channels.auth import AuthMiddlewareStack
import chat.routing  
from chat.jwt_middleware  import JWTAuthMiddleware 

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'api.settings')

app = ProtocolTypeRouter(
    {
        'http': get_asgi_application(),
        'websocket': 
        JWTAuthMiddleware(
            URLRouter(
                chat.routing.websocket_urlpatterns
            )
        )
    }
)
