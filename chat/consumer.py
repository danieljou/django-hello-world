#...............................................................................................................Imports..............................................................................................#
#                                                                                                                                                                                                                    #
#                                                                                                                                                                                                                    #
#....................................................................................................................................................................................................................#

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from chat.models import Message
from channels.db import database_sync_to_async
from django.utils.text import slugify
from asgiref.sync import async_to_sync , sync_to_async
from django.db.models import Q, Exists, OuterRef, Subquery
from django.shortcuts import get_object_or_404
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.functions import Coalesce
from .serializers import (
	UserSerializer, 
	SearchSerializer, 
	RequestSerializer, 
	FriendSerializer,
	MessageSerializer
)
from .models import User, Connection, Message



class ChatConsumer(AsyncWebsocketConsumer):

#...............................................................................................................@database_sync_to_async..............................................................................#
#                                                                                                                                                                                                                    #
#                                                                                                                                                                                                                    #
#....................................................................................................................................................................................................................#
    @database_sync_to_async
    def update_connection(self, connection):
        print(f"Updating connection {connection.id} to accepted")
        connection.accepted = True
        connection.save()
        return connection
    
    @database_sync_to_async
    def serialize_request(self, connection):
        """
        Serialize a single connection for request updates
        """
        return RequestSerializer(connection).data
    
    @database_sync_to_async
    def serialize_friend(self, connection, user):
        return FriendSerializer(
            connection,
            context={'user': user}
        ).data
    
    @database_sync_to_async
    def create_message(self, sender_id, receiver_id, message):
        """Create a message in the database"""
        return Message.objects.create(
            sender_id=sender_id,
            receiver_id=receiver_id,  
            message=message
        )

    @database_sync_to_async
    def message_information(self, message_data):
        """Prepare message information for sending"""
        message = message_data['message']
        return {
            "message": message.message,
            "sender": message_data.get('user', 'Unknown'),
            "sender_id": message.sender_id,
            "receiver_id": message.receiver_id
        }
    
    @database_sync_to_async
    def get_connections(self, user_identifier):
        """
        Get all pending connection requests for a user
        Can accept either a username string or User instance
        """
        try:
            if isinstance(user_identifier, str):
                return list(Connection.objects.select_related('sender', 'receiver').filter(
                    receiver__username=user_identifier,
                    accepted=False
                ))
            else:
                return list(Connection.objects.select_related('sender', 'receiver').filter(
                    receiver=user_identifier,
                    accepted=False
                ))
        except Exception as e:
            print(f"Error fetching connections: {str(e)}")
            return []
    
    @database_sync_to_async
    def get_users(self, query):
        """
        Search for users by username, first name, or last name
        Excludes the current user and limits results for performance
        """
        try:
            return User.objects.filter(
                Q(username__istartswith=query) |
                Q(first_name__istartswith=query) |
                Q(last_name__istartswith=query)
            ).exclude(
                username=self.scope['user_id'].username
            ).select_related(
                'profile'  # If you have a profile model
            ).only(
                'id', 'username', 'first_name', 'last_name', 'email'
            ).distinct()[
                :20  # Limit results for performance
            ]
        except Exception as e:
            print(f"Error in get_users: {str(e)}")
            return User.objects.none()
    
    @database_sync_to_async
    def get_user(self, username):
            """Fetch a user by username."""
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                return None
            
    @database_sync_to_async
    def get_or_create_connection(self, sender_id, receiver):
            """Fetch or create a connection between the sender and receiver."""
            sender = User.objects.get(id=sender_id)  # Get the User instance
            connection, created = Connection.objects.get_or_create(sender=sender, receiver=receiver)
            return connection, created

    @database_sync_to_async
    def serialize_connection(self, connection):
        """Serialize the connection object."""
        return RequestSerializer(connection).data
    

    @database_sync_to_async
    def get_connection_by_username(self, username):
        """
        Get a single connection by username
        """
        try:
            # First try looking for the connection where the given username is the receiver
            connection = Connection.objects.select_related('sender', 'receiver').filter(
                receiver__username=username,
                accepted=False
            ).first()
            
            if connection:
                print(f"Found connection where {username} is receiver")
                return connection
                
            # If not found, try looking where the username is the sender
            connection = Connection.objects.select_related('sender', 'receiver').filter(
                sender__username=username,
                accepted=False
            ).first()
            
            if connection:
                print(f"Found connection where {username} is sender")
                return connection
                
            print(f"No connection found for username: {username}")
            return None
            
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
        """
        Get messages, friend data, and pagination info for a connection
        """
        page_size = 15
        
        # Get connection
        connection = Connection.objects.get(id=connection_id)
        
        # Get messages with pagination
        messages = Message.objects.filter(
            connection=connection
        ).order_by('-created')[page * page_size:(page + 1) * page_size]
        
        # Serialize messages
        serialized_messages = MessageSerializer(
            messages,
            context={'user': user},
            many=True
        )
        
        # Get recipient friend
        recipient = connection.receiver if connection.sender == user else connection.sender
        
        # Serialize friend
        serialized_friend = UserSerializer(recipient)
        
        # Get total message count for pagination
        messages_count = Message.objects.filter(
            connection=connection
        ).count()
        
        # Calculate next page
        next_page = page + 1 if messages_count > (page + 1) * page_size else None
        
        # Return compiled data
        return {
            'messages': serialized_messages.data,
            'next': next_page,
            'friend': serialized_friend.data
        }
    

    @database_sync_to_async
    def create_and_get_message_data(self, connection_id, user, message_text):
        try:
            # Get connection
            connection = Connection.objects.get(id=connection_id)
            
            # Create message
            message = Message.objects.create(
                connection=connection,
                user=user,
                text=message_text
            )
            
            # Determine recipient
            recipient = connection.receiver if connection.sender == user else connection.sender
            
            # Prepare serialized data for both sender and recipient
            sender_message = MessageSerializer(
                message,
                context={'user': user}
            ).data
            
            recipient_message = MessageSerializer(
                message,
                context={'user': recipient}
            ).data
            
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


#...............................................................................................................Def of Async functions...............................................................................#
#                                                                                                                                                                                                                    #
#                                                                                                                                                                                                                    #
#....................................................................................................................................................................................................................#

   
    async def connect(self):
        user_id = self.scope["user_id"]
        user = await sync_to_async(User.objects.get)(pk=user_id)
        print(f"User: {user}")  # Debugging output
        if user is None or not user.is_authenticated:
            print("User is not authenticated.")
            return

        self.username = user.username  
        await self.channel_layer.group_add(
            self.username, self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        # Check if username is set before trying to use it
        if hasattr(self, 'username'):
            await self.channel_layer.group_discard(
                self.username, self.channel_name
            )
        print(f"WebSocket closed with code: {close_code}")


    async def chat_message(self, event):   
        await self.send(text_data=json.dumps(event['message_info']))


    def is_error_exists(self):
        """Check if error exists during websocket connection"""
        return 'error' in self.scope

    async def receive(self, text_data):
        data = json.loads(text_data)
        data_source = data.get('source')
        print("data_source ", data_source)  
        print('receive', json.dumps(data, indent=2))
        if data_source == 'friend.list':
            await self.receive_friend_list(data)
        elif data_source == 'message.list':
            await self.receive_message_list(data)
        elif data_source == 'message.send':
            await self.receive_message_send(data)
        elif data_source == 'message.type':
            await self.receive_message_type(data)
        elif data_source == 'request.accept':
            await self.receive_request_accept(data)
        elif data_source == 'request.connect':
            print(".................................conn..........................................")
            await self.receive_request_connect(data)
            print(".................................req...........................................")
        elif data_source == 'request.list':
            print(".................................in req list...................................")
            await self.receive_request_list(data)
            print(".........................going to re_list Func..................................")
        elif data_source == 'search':
            await self.receive_search(data)  
        elif data_source == 'thumbnail':
            self.receive_thumbnail(data)




    async def receive_friend_list(self, data):
        print("inside friend list")
        user = self.scope['user']
        
        try:
            # Get and serialize connections asynchronously
            connections_data = await self.get_friend_connections(user)
            
            # Send data back to requesting user
            await self.send_group(user.username, 'friend.list', connections_data)
            
        except Exception as e:
            error_msg = f"Error fetching friend list: {str(e)}"
            print(error_msg)
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'friend.list',
                'message': error_msg
            }))



    async def receive_request_list(self, data):
        print("in req_list function")
        user = data.get('username')

        if not user:
            user = self.scope['user'].username  # Fallback to current user if username not provided

        try:
            # Get all connections
            connections = await self.get_connections(user)
            print(f"Found {len(connections)} connections for user {user}")
            
            # Serialize connections one by one asynchronously
            serialized_connections = []
            for connection in connections:
                serialized = await self.serialize_connection(connection)
                serialized_connections.append(serialized)
            
            print(f"Serialized {len(serialized_connections)} connections")
            
            await self.send(text_data=json.dumps({
                'type': 'send_data',
                'source': 'request.list',
                'data': serialized_connections
            }))
            
        except Exception as e:
            error_msg = f"Error in receive_request_list: {str(e)}"
            print(error_msg)
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
        print("test")
        response = {
            'type': 'broadcast_group',
            'source': source,
            'data': data
        }
        await self.channel_layer.group_send(
            group, response
        )


        

    async def broadcast_group(self, data):
        '''
        data:
            - type: 'broadcast_group'
            - source: where it originated from
            - data: what ever you want to send as a dict
        '''
        data.pop('type')
        '''
        return data:
            - source: where it originated from
            - data: what ever you want to send as a dict
        '''
        await self.send(text_data=json.dumps(data))
        



#...............................................................................................................MESSAGES AND FRIENDSHIP..............................................................................#
#                                                                                                                                                                                                                    #
#                                                                                                                                                                                                                    #
#....................................................................................................................................................................................................................#

    async def receive_message_list(self, data):
        user = self.scope['user']
        connection_id = data.get('connectionId')
        page = data.get('page', 0)
        
        try:
            # Get messages and related data asynchronously
            message_data = await self.get_message_data(connection_id, page, user)
            
            # Send back to the requestor
            await self.send_group(user.username, 'message.list', message_data)
            
        except ObjectDoesNotExist as e:
            print(f'Error: could not find connection {connection_id}')
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'message.list',
                'message': 'Connection not found'
            }))
        except Exception as e:
            print(f'Error fetching messages: {str(e)}')
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
            # Create message and get necessary data
            message_data = await self.create_and_get_message_data(
                connection_id,
                user,
                message_text
            )
            
            if not message_data:
                return
                
            # Send to sender
            await self.send_group(
                message_data['sender'].username,
                'message.send',
                {
                    'message': message_data['sender_message'],
                    'friend': message_data['sender_friend']
                }
            )
            
            # Send to recipient
            await self.send_group(
                message_data['recipient'].username,
                'message.send',
                {
                    'message': message_data['recipient_message'],
                    'friend': message_data['recipient_friend']
                }
            )
            
        except Exception as e:
            error_msg = f'Failed to send message: {str(e)}'
            print(f'Error: {error_msg}')
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'message.send',
                'message': error_msg
            }))


    async def receive_message_type(self, data):
        user = self.scope['user']
        recipient_username = data.get('username')
        
        try:
            # Send typing indicator
            await self.send_group(
                recipient_username,
                'message.type',
                {
                    'username': user.username
                }
            )
            
        except Exception as e:
            print(f'Error sending typing indicator: {str(e)}')
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'message.type',
                'message': f'Failed to send typing indicator: {str(e)}'
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
        print("receive_request_accept")
        username = data.get('username')
        print(f"Attempting to accept request for username: {username}")
        
        try:
            # Get single connection by username
            connection = await self.get_connection_by_username(username)
            if not connection:
                raise ObjectDoesNotExist("Connection not found")
                
            print(f"Found connection: sender={connection.sender.username}, receiver={connection.receiver.username}")
            
            # Update the connection
            connection = await self.update_connection(connection)
            serialized = await self.serialize_request(connection)
            
            # Send to sender
            await self.send_group(
                connection.sender.username, 
                'request.accept', 
                serialized
            )
            
            # Send to receiver
            await self.send_group(
                connection.receiver.username, 
                'request.accept',
                serialized
            )
            
            # Handle friend serialization and notifications
            serialized_friend_sender = await self.serialize_friend(
                connection,
                connection.sender
            )
            await self.send_group(
                connection.sender.username,
                'friend.new',
                serialized_friend_sender
            )
            
            serialized_friend_receiver = await self.serialize_friend(
                connection,
                connection.receiver
            )
            await self.send_group(
                connection.receiver.username,
                'friend.new',
                serialized_friend_receiver
            )
            
        except ObjectDoesNotExist as e:
            error_msg = f'Connection not found for username: {username}'
            print(f'Error1: {error_msg}')
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'request.accept',
                'message': error_msg
            }))
        except Exception as e:
            error_msg = f'Failed to accept connection for {username}: {str(e)}'
            print(f'Error2: {error_msg}')
            await self.send(text_data=json.dumps({
                'type': 'error',
                'source': 'request.accept',
                'message': error_msg
            }))