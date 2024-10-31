from flask import request
from myapp import create_app
from myapp.database import db, Message, ChatMessage
from flask_socketio import emit, join_room, leave_room
import os
import google.generativeai as genai

app, socket = create_app()
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

# Create the model and configuration for Gemini API
generation_config = {
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
    "response_mime_type": "text/plain",
}

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
)

# A list to hold conversation history
conversation_history = []

# Function to add message to conversation history
def add_to_conversation_history(sender_username, original_message, translated_message, timestamp):
    # Create a message record
    message_record = {
        "sender_username": sender_username,
        "original_message": original_message,
        "translated_message": translated_message,
        "timestamp": timestamp,
    }
    # Add the new record to the conversation history
    conversation_history.append(message_record)
    # Limit the conversation history to the last 10 messages
    if len(conversation_history) > 10:
        conversation_history.pop(0)  # Remove the oldest message

# Function to translate message with validation against recent peer message
def translate_message_with_context(message, language_code, sender_username):
    # Build the conversation context for Gemini
    chat_context = [
        {"parts": [{"text": msg['original_message']}], "role": "user"}
        for msg in conversation_history
    ]
    
    # Retrieve the last peer's message for context
    last_peer_message = next(
        (msg for msg in reversed(conversation_history) if msg["sender_username"] != sender_username), 
        None
    )
    
    # Check if last_peer_message is None and provide default context if necessary
    if last_peer_message:
        peer_context = (
            f"Original: {last_peer_message['original_message']}\n"
            f"Translation: {last_peer_message['translated_message']}\n\n"
        )
    else:
        peer_context = "No previous context.\n\n"

        # Command Gemini to translate with validation
    validation_prompt = (
        f"Here is the chat history:\n{peer_context}\n\n"
        f"Translate this new message from {sender_username} into {language_code}: {message}.\n"
        f"Consider the last peer's message:\n\n{peer_context}"
        f"As you translate the sender's message to the intended language_code, make sure the response at least corresponds to the original peer's last message context. "
        f"Additionally, ensure that the tone of the sender's message is preserved in the translation. "
        f"Remember, we only need the translation without any description or extra information."
    )


    # Start a chat session with context
    chat_session = model.start_chat(history=chat_context)
    
    # Get response with validation
    try:
        response = chat_session.send_message({"parts": [{"text": validation_prompt}]})
        return response.text.strip().split('\n')[0]  # Return only the first line
    except Exception as e:
        print(f"Error in translation: {str(e)}")
        return "Translation error occurred."

# COMMUNICATION ARCHITECTURE
@socket.on("join-chat")
def join_private_chat(data):
    room = data["rid"]
    join_room(room=room)
    socket.emit(
        "joined-chat",
        {"msg": f"{room} is now online."},
        room=room,
    )

@socket.on("outgoing")
def chatting_event(json, methods=["GET", "POST"]):
    room_id = json["rid"]
    timestamp = json["timestamp"]
    message = json["message"]
    sender_id = json["sender_id"]
    sender_username = json["sender_username"]

    # Retrieve selected language from the client
    selected_language = json.get("selected_language", "en")

    # Translate the message only for other users with conversation context
    translated_message = translate_message_with_context(message, selected_language, sender_username)

    # Get the message entry for the chat room
    message_entry = Message.query.filter_by(room_id=room_id).first()

    # Add the new message to the conversation
    chat_message = ChatMessage(
        content=message,
        timestamp=timestamp,
        sender_id=sender_id,
        sender_username=sender_username,
        room_id=room_id,
    )
    message_entry.messages.append(chat_message)

    # Update the database
    try:
        chat_message.save_to_db()
        message_entry.save_to_db()
    except Exception as e:
        print(f"Error saving message to the database: {str(e)}")
        db.session.rollback()

    # Add the original and translated message to conversation history
    add_to_conversation_history(sender_username, message, translated_message, timestamp)

    # Emit the translated message to other users
    socket.emit(
        "message",
        {
            "message": translated_message,
            "timestamp": timestamp,
            "sender_username": sender_username
        },
        room=room_id,
        include_self=False,
    )
if __name__ == "__main__":
    socket.run(app, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True, debug=True)
