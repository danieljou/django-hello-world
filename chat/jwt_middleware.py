from channels.middleware import BaseMiddleware
from rest_framework_simplejwt.tokens import AccessToken
from channels.db import database_sync_to_async

from .models import User

class JWTAuthMiddleware(BaseMiddleware):
    
    async def __call__(self, scope, receive, send):
        token = self.get_token_from_scope(scope)
        print('TOKEN ', token)

        if token is not None:
            user_id = await self.get_user_from_token(token) 
            if user_id:
                print('USER ID :', user_id)
                scope['user_id'] = user_id
                # Optionally fetch the user instance
                scope['user'] = await self.get_user_instance(user_id)

            else:
                scope['error'] = 'Invalid token'
        else:
            scope['error'] = 'Provide an auth token'    

        # Ensure that we call the next layer in the middleware stack
        return await super().__call__(scope, receive, send)

    def get_token_from_scope(self, scope):
        # Extract the query string from the scope
        query_string = scope.get("query_string", b"").decode("utf-8")
        
        # Parse the query string to get parameters
        params = dict(q.split('=') for q in query_string.split('&') if '=' in q)
        
        # Return the token if it exists
        return params.get('token', None)

    @database_sync_to_async
    def get_user_from_token(self, token):
        try:
            access_token = AccessToken(token)
            return access_token['user_id']
        except Exception as e:
            print(f"Token error: {e}")
            return None

    @database_sync_to_async
    def get_user_instance(self, user_id):
        """Fetch the user instance from the database."""
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None