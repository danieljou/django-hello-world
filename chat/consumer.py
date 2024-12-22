from channels.generic.websocket import AsyncWebsocketConsumer
from django.db.models import Q, Exists, OuterRef
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.db.models.functions import Coalesce
from channels.db import database_sync_to_async
from .models import User, Connection, Message
from asgiref.sync import sync_to_async
from django.utils.text import slugify
from .serializers import (
    UserSerializer, 
    SearchSerializer, 
    RequestSerializer, 
    FriendSerializer,
    MessageSerializer
)
import base64
import json


class ChatConsumer(AsyncWebsocketConsumer):

    @database_sync_to_async
    def update_connection(self, connection):
        connection.accepted = True
        connection.save()
        return connection
    
    @database_sync_to_async
    def serialize_request(self, connection):
        return RequestSerializer(connection).data
    
    @database_sync_to_async
    def serialize_friend(self, connection, user):
        return FriendSerializer(connection, context={'user': user}).data
    
    @database_sync_to_async
    def create_message(self, sender_id, receiver_id, message):
        return Message.objects.create(
            sender_id=sender_id,
            receiver_id=receiver_id,  
            text=message
        )

    @database_sync_to_async
    def message_information(self, message_data):
        message = message_data['message']
        return {
            "message": message.text,
            "sender": message_data.get('user', 'Unknown'),
            "sender_id": message.sender_id,
            "receiver_id": message.receiver_id
        }
    
    @database_sync_to_async
    def get_connections(self, user_identifier):
        try:
            return list(Connection.objects.select_related('sender', 'receiver').filter(
                receiver__username=user_identifier,
                accepted=False
            ))
        except Exception as e:
            print(f"Error fetching connections: {str(e)}")
            return []
    
    @database_sync_to_async
    def get_users(self, query):
        user = self.scope['user']  # Get the current user
        try:
            return User.objects.filter(
                Q(username__istartswith=query) |
                Q(first_name__istartswith=query) |
                Q(last_name__istartswith=query)
            ).exclude(username=user.username).distinct()[:20]
        except Exception as e:
            print(f"Error in get_users: {str(e)}")
            return User.objects.none()

    async def receive_search(self, data):
        user_id = self.scope.get('user_id')
        user = await sync_to_async(User.objects.get)(pk=user_id)

        query = data.get('query')

        users = await sync_to_async(lambda: list(User.objects.filter(
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query)
        ).exclude(username=user.username)))()

        # Pass the current user as context
        serialized = SearchSerializer(users, many=True, context={'user': user})
        await self.send_group(self.username, 'search', serialized.data)
    
    @database_sync_to_async
    def get_user(self, username):
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist:
            return None
            
    @database_sync_to_async
    def get_or_create_connection(self, sender_id, receiver):
        sender = User.objects.get(id=sender_id)
        connection, created = Connection.objects.get_or_create(sender=sender, receiver=receiver)
        return connection, created

    @database_sync_to_async
    def serialize_connection(self, connection):
        return RequestSerializer(connection).data
    
    @database_sync_to_async
    def get_connection_by_username(self, username):
        try:
            connection = Connection.objects.select_related('sender', 'receiver').filter(
                Q(receiver__username=username) | Q(sender__username=username),
                accepted=False
            ).first()
            return connection
        except Exception as e:
            print(f"Error in get_connection_by_username: {str(e)}")
            return None
        
    
    @database_sync_to_async
    def get_friend_connections(self, user):
        """
        Get and serialize friend connections with latest messages
        """
        try:
            # Latest message subquery
            latest_message = Message.objects.filter(
                connection=OuterRef('id')
            ).order_by('-created')[:1]
            
            # Get connections for user
            connections = Connection.objects.filter(
                Q(sender=user) | Q(receiver=user),
                accepted=True
            ).annotate(
                latest_text=latest_message.values('text'),
                latest_created=latest_message.values('created')
            ).order_by(
                Coalesce('latest_created', 'updated').desc()
            )
            
            # Serialize the connections
            serialized = FriendSerializer(
                connections,
                context={'user': user},
                many=True
            )
            
            return serialized.data
            
        except Exception as e:
            print(f"Error in get_friend_connections: {str(e)}")
            return []

    @database_sync_to_async
    def get_message_data(self, connection_id, page, user):
        page_size = 15
        connection = Connection.objects.get(id=connection_id)
        messages = Message.objects.filter(connection=connection).order_by('-created')[page * page_size:(page + 1) * page_size]
        serialized_messages = MessageSerializer(messages, context={'user': user}, many=True)
        recipient = connection.receiver if connection.sender == user else connection.sender
        serialized_friend = UserSerializer(recipient)
        messages_count = Message.objects.filter(connection=connection).count()
        next_page = page + 1 if messages_count > (page + 1) * page_size else None
        
        return {
            'messages': serialized_messages.data,
            'next': next_page,
            'friend': serialized_friend.data
        }

    @database_sync_to_async
    def create_and_get_message_data(self, connection_id, user, message_text):
        try:
            connection = Connection.objects.get(id=connection_id)
            message = Message.objects.create(connection=connection, user=user, text=message_text)
            recipient = connection.receiver if connection.sender == user else connection.sender
            
            sender_message = MessageSerializer(message, context={'user': user}).data
            recipient_message = MessageSerializer(message, context={'user': recipient}).data
            sender_friend = UserSerializer(recipient).data
            recipient_friend = UserSerializer(user).data
            
            return {
                'sender': user,
                'recipient': recipient,
                'sender_message': sender_message,
                'recipient_message': recipient_message,
                'sender_friend': sender_friend,
                'recipient_friend': recipient_friend
            }
            
        except Connection.DoesNotExist:
            print(f'Error: could not find connection {connection_id}')
            return None
        except Exception as e:
            print(f'Error creating message: {str(e)}')
            raise

    async def connect(self):
        user_id = self.scope["user_id"]
        user = await sync_to_async(User.objects.get)(pk=user_id)
        if user is None or not user.is_authenticated:
            return

        self.username = user.username  
        await self.channel_layer.group_add(self.username, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'username'):
            await self.channel_layer.group_discard(self.username, self.channel_name)

    async def chat_message(self, event):   
        await self.send(text_data=json.dumps(event['message_info']))

    async def receive(self, text_data):
        data = json.loads(text_data)
        data_source = data.get('source')
        
        if data_source == 'friend.list':
            await self.receive_friend_list(data)
        elif data_source == 'message.list':
            await self.receive_message_list(data)
        elif data_source == 'message.send':
            await self.receive_message_send(data)
        elif data_source == 'request.accept':
            await self.receive_request_accept(data)
        elif data_source == 'request.connect':
            await self.receive_request_connect(data)
        elif data_source == 'request.list':
            await self.receive_request_list(data)
        elif data_source == 'search':
            await self.receive_search(data)

    async def receive_friend_list(self, data):
        user = self.scope['user']
        try:
            connections_data = await self.get_friend_connections(user)
            await self.send_group(user.username, 'friend.list', connections_data)
        except Exception as e:
            error_msg = f"Error fetching friend list: {str(e)}"
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'friend.list',
                'message': error_msg
            }))

    async def receive_request_list(self, data):
        user = data.get('username') or self.scope['user'].username
        try:
            connections = await self.get_connections(user)
            serialized_connections = [await self.serialize_connection(connection) for connection in connections]
            await self.send(text_data=json.dumps({
                'type': 'send_data',
                'source': 'request.list',
                'data': serialized_connections
            }))
        except Exception as e:
            error_msg = f"Error in receive_request_list: {str(e)}"
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'request.list',
                'message': error_msg
            }))

            

    async def receive_search(self, data):
        user_id = self.scope.get('user_id')
        user = await sync_to_async(User.objects.get)(pk=user_id)
        print(f"User: {user}")  # Debugging output

        if user is None or not user.is_authenticated:
            print("User is no more authenticated.")
            return

        print("data research")
        query = data.get('query')

        users = await sync_to_async(lambda: list(User.objects.filter(
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query)
        ).exclude(username=user.username)  # Exclude the current user
        .annotate(
            pending_them=Exists(
                Connection.objects.filter(
                    sender=user,
                    receiver=OuterRef('id'),
                    accepted=False
                )
            ),
            pending_me=Exists(
                Connection.objects.filter(
                    sender=OuterRef('id'),
                    receiver=user,
                    accepted=False
                )
            ),
            connected=Exists(
                Connection.objects.filter(
                    Q(sender=user, receiver=OuterRef('id')) |
                    Q(receiver=user, sender=OuterRef('id')),
                    accepted=True
                )
            )
        )))()

        serialized = SearchSerializer(users, many=True)
        print("DARA ", serialized.data)
        
        # Use the send_group method to send search results
        await self.send_group(self.username, 'search', serialized.data)

    async def send_group(self, group, source, data):
        response = {
            'type': 'broadcast_group',
            'source': source,
            'data': data
        }
        await self.channel_layer.group_send(group, response)

    async def broadcast_group(self, data):
        data.pop('type')
        await self.send(text_data=json.dumps(data))

    async def receive_message_list(self, data):
        user = self.scope['user']
        connection_id = data.get('connectionId')
        page = data.get('page', 0)
        
        try:
            message_data = await self.get_message_data(connection_id, page, user)
            await self.send_group(user.username, 'message.list', message_data)
        except ObjectDoesNotExist:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'message.list',
                'message': 'Connection not found'
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'message.list',
                'message': f'Failed to fetch messages: {str(e)}'
            }))

    async def receive_message_send(self, data):
        user = self.scope['user']
        connection_id = data.get('connectionId')
        message_text = data.get('message')
        
        try:
            message_data = await self.create_and_get_message_data(connection_id, user, message_text)
            if not message_data:
                return
            
            await self.send_group(message_data['sender'].username, 'message.send', {
                'message': message_data['sender_message'],
                'friend': message_data['sender_friend']
            })
            await self.send_group(message_data['recipient'].username, 'message.send', {
                'message': message_data['recipient_message'],
                'friend': message_data['recipient_friend']
            })
        except Exception as e:
            error_msg = f'Failed to send message: {str(e)}'
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'message.send',
                'message': error_msg
            }))


    async def receive_request_connect(self, data):
        username = data.get('username')

        # Fetch the receiver user
        receiver = await self.get_user(username)
        if not receiver:
            print('Error: User not found')
            return

        sender_id = self.scope['user'].id

        try:
            # Create or retrieve the connection
            connection, created = await self.get_or_create_connection(sender_id, receiver)

            if created:
                print(f"New Connection Created:")
                print(f"ID: {connection.id}")
                print(f"Sender: {connection.sender.username}")
                print(f"Receiver: {connection.receiver.username}")
                print(f"Accepted: {connection.accepted}")
                print(f"Created: {connection.created}")
            else:
                print("Connection already exists.")

            # Serialize the connection object
            serialized = await self.serialize_connection(connection)

            # Send data to the current user's WebSocket
            await self.send(text_data=json.dumps({
                'type': 'send_data',
                'source': 'request.connect',
                'data': serialized
            }))

            # Send data to the receiver's group
            await self.channel_layer.group_send(
                f"user-{slugify(receiver.username)}",
                {
                    'type': 'send_data',
                    'source': 'request.connect',
                    'data': serialized
                }
            )

        except Exception as e:
            print(f"Error in receive_request_connect: {str(e)}")
            # You might want to send an error message back to the client here
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'request.connect',
                'message': 'Failed to create connection'
            })) 

    async def receive_request_accept(self, data):
        username = data.get('username')
        
        try:
            connection = await self.get_connection_by_username(username)
            if not connection:
                raise ObjectDoesNotExist("Connection not found")
                
            connection = await self.update_connection(connection)
            serialized = await self.serialize_request(connection)

            await self.send_group(connection.sender.username, 'request.accept', serialized)
            await self.send_group(connection.receiver.username, 'request.accept', serialized)
            
            serialized_friend_sender = await self.serialize_friend(connection, connection.sender)
            await self.send_group(connection.sender.username, 'friend.new', serialized_friend_sender)
            
            serialized_friend_receiver = await self.serialize_friend(connection, connection.receiver)
            await self.send_group(connection.receiver.username, 'friend.new', serialized_friend_receiver)
            
        except ObjectDoesNotExist:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'request.accept',
                'message': 'Connection not found'
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'request.accept',
                'message': f'Failed to accept connection for {username}: {str(e)}'
            }))

    def receive_thumbnail(self, data):
        user = self.scope['user_id']
        image_str = data.get('base64')
        image = ContentFile(base64.b64decode(image_str))
        filename = data.get('filename')
        user.thumbnail.save(filename, image, save=True)
        serialized = UserSerializer(user)
        self.send_group(self.username, 'thumbnail', serialized.data)

