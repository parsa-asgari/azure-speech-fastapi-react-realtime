from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv
from datetime import datetime
import os
import asyncio

load_dotenv() # Load the environmental variables from .env file

class bcolors: # Only to apply colors to the prints
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

app = FastAPI() # Create a new FastAPI app

# Speech key and region from your Azure Speech Recognition service
speech_key = os.getenv("SPEECH_KEY")
speech_region = os.getenv("SPEECH_REGION")

def create_speech_recognizer(loop, queue):
    """
    Allows to create a client for the speech recognizer and a stream (buffer)
    """
    # Create the configuration of the recognizer from your account of Azure
    speech_config = speechsdk.SpeechConfig(
        subscription=speech_key,
        region=speech_region
    )

    # Configuration for the input audio format. The documentation specifies that you can use GStreamer (It also needs to be installed locally) to encode other formats to PCM (Pulse Code Modulation).
    # Here I'm using speechsdk.AudioStreamContainerFormat.ANY since i'm sending streaming data using the audio/webm;codecs:opus format directl, that is supported for most of the modern browsers
    format = speechsdk.audio.AudioStreamFormat(compressed_stream_format=speechsdk.AudioStreamContainerFormat.ANY) # To receive audio data in any format and process them with GStreamer
    stream = speechsdk.audio.PushAudioInputStream(format) # Creates an audio stream to send data to the speech service
    audio_config = speechsdk.audio.AudioConfig(stream=stream) # Adjust the audio config using the recently created stream

    # Creates a speech recognizer client for the speech recognizer
    speech_recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
        language="en-US" # Change to your desired language if supported. If not specified, 'en-US' will be used by default.
    )

    # Callbacks for the speech recognizer. They are automatically triggered based on event type
    def recognizing_cb(evt: speechsdk.SpeechRecognitionEventArgs):
        """
        Triggered everytime the recognizer has processed a set of audio chunks and recognized part of the speech
        """
        print(f"{bcolors.OKGREEN}Azure Speech Recognition -> Recognizing: {evt.result.text}{bcolors.ENDC}")

    def recognized_cb(evt: speechsdk.SpeechRecognitionEventArgs):
        """
        Triggered when the speech recognition has processed an audio fragment and recognized the text in its entirety
        """
        print(f"{bcolors.OKGREEN}Azure Speech Recognition -> Recognized: {evt.result.text}{bcolors.ENDC}")
        asyncio.run_coroutine_threadsafe(queue.put(evt.result.text), loop)

    def stop_cb(evt: speechsdk.SessionEventArgs):
        """
        Triggered when speech recognition session is stopped
        """
        print(f"{bcolors.WARNING}Azure Speech Recognition -> Session stopped due websocket close: {evt}{bcolors.ENDC}")

    def canceled_cb(evt: speechsdk.SessionEventArgs):
        """
        Triggered when the speech recognition session is cancelled due to an error
        """
        print(f"{bcolors.FAIL}Azure Speech Recognition -> Session canceled due an error: {evt}{bcolors.ENDC}")

    # Connect callbacks to the speech recognizer to be triggered when an event occurs.
    speech_recognizer.recognizing.connect(recognizing_cb)
    speech_recognizer.recognized.connect(recognized_cb)
    speech_recognizer.session_stopped.connect(stop_cb)
    speech_recognizer.canceled.connect(canceled_cb)

    return speech_recognizer, stream

@app.websocket("/ws") # Change to your desired websocket endpoint
async def audio_streaming(websocket: WebSocket):
    await websocket.accept() # Accept client connection

    loop = asyncio.get_event_loop() # Get the asyncio event loop
    message_queue = asyncio.Queue() # Create a message queue to store the results of speech recognition

    speech_recognizer, stream = create_speech_recognizer(loop, message_queue) # Create the speech recognizer and the audio stream

    async def receive_audio(websocket, stream):
        audio_data = b"" # Store the audio data in bytes
        print(f"{bcolors.OKGREEN}WebSocket -> Receiving audio from client and saving into stream...{bcolors.ENDC}")
        while True: # As long as the customer is connected
            try: # Attempt to receive audio data from the client
                data = await websocket.receive_bytes()  # Receive audio data from the client
                audio_data += data  # Store audio all data chunks in a variable

                stream.write(data)  # Write audio data to the stream buffer

                print(f"{bcolors.OKCYAN}WebSocket -> Stream data en bytes: {len(data)}{bcolors.ENDC}", end="\n")  # Data that are being sent from the client
            except WebSocketDisconnect:  # If the client is disconnected
                print(f"{bcolors.FAIL}Azure Speech Recognition -> Stream closed{bcolors.ENDC}")
                stream.close()  # Close the stream

                print(f"{bcolors.OKBLUE}API -> Websocket client disconnected!{bcolors.ENDC}")
                print(f"{bcolors.OKBLUE}API -> Stopping continuous recognition...{bcolors.ENDC}")
                speech_recognizer.stop_continuous_recognition() # Stop speech recognition
                print(f"{bcolors.OKBLUE}API -> Continuous recognition stopped!{bcolors.ENDC}")
                print(f"{bcolors.OKBLUE}API -> Exporting audio data to a file...{bcolors.ENDC}")
                # Save received audio data to a file
                with open(f"records/received_audio_{datetime.now()}.webm", "wb") as f: # Create an audio file in webm format
                    f.write(audio_data) # Write the whole audio data to the file
                    print(f"{bcolors.OKBLUE}API -> Audio data exported!{bcolors.ENDC}")
                break
            except Exception as e: # If an error occurs
                print(f"Error: {e}")
                break # Exiting the loop

    async def send_messages():
        """
        Allows messages recognized by the Azure service to be sent to the client via the websocket to the client
        """
        while True: # As long as the customer is connected
            message = await message_queue.get() # Get the recognized text from the queue
            await websocket.send_text(message) # Send the text to the websocket client

    try:
        speech_recognizer.start_continuous_recognition() # Start continuous speech recognition
        print("API -> Continuous recognition running, say something to process data...")
        await asyncio.gather(receive_audio(websocket, stream), send_messages()) # Execute the functions of receiving audio and sending messages back to the client.

    except Exception as e:
        print(f"Error: {e}")
