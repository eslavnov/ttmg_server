from google.api_core.exceptions import GoogleAPIError
from google.cloud import texttospeech
import openai
from elevenlabs.client import ElevenLabs
import wave

from typing import AsyncGenerator
from wyoming.client import AsyncTcpClient
from wyoming.audio import AudioChunk, AudioStop
from wyoming.tts import Synthesize, SynthesizeVoice
import io
import asyncio
import socket

async def tts_stream_google(sentence: str, credentials_path: str, name: str, language_code: str, gender: str, logger):
    """Calls Google Cloud TTS and streams back audio."""
    try:
        client = texttospeech.TextToSpeechClient.from_service_account_json(credentials_path)
        input_text = texttospeech.SynthesisInput(text=sentence)
        voice = texttospeech.VoiceSelectionParams(
            name=name,
            language_code=language_code,
            ssml_gender = texttospeech.SsmlVoiceGender.FEMALE if gender == "FEMALE" else texttospeech.SsmlVoiceGender.MALE
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        response = client.synthesize_speech(
            input=input_text, voice=voice, audio_config=audio_config
        )
        yield response.audio_content
    except GoogleAPIError as e:
        logger.error(f"Google Cloud TTS API error: {e}")
        yield b""  # Yield an empty byte string to indicate an error
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        yield b""  # Yield an empty byte string to indicate an error

async def tts_stream_openai(sentence: str, model: str, voice: str, logger):
    """Calls OpenAI TTS, loads full audio (OpenAI does not support streaming), and streams it in chunks."""
    try:
        response = openai.audio.speech.create(
            model=model,
            voice=voice,
            input=sentence,
            response_format="mp3"
        )
        # Stream response in chunks
        for audio_chunk in response.iter_bytes(1024):
          yield audio_chunk

    except openai.OpenAIError as e:
        logger.error(f"OpenAI TTS API error: {e}")
        yield b""  # Return an empty byte string on error
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        yield b""  # Return an empty byte string on error
        
async def tts_stream_elevenlabs(sentence: str, model: str, voice: str, api_key: str, logger):
    """Calls ElevenLabs TTS and streams back audio."""
    try:
        client = ElevenLabs(api_key=api_key)
        response = client.text_to_speech.convert_as_stream(
            text=sentence,
            voice_id=voice,
            model_id=model
        )
        # Stream response in chunks
        for audio_chunk in response:
            yield audio_chunk

    except Exception as e:
        logger.error(f"ElevenLabs TTS API error: {e}")
        yield b""  # Return an empty byte string on error
  
async def tts_stream_piper(sentence: str, voice_name: str, host: str, port:str, logger) -> AsyncGenerator[bytes, None]:
    """Calls Piper, loads full audio (it does not support streaming), and streams it in chunks."""
    try:
      async with AsyncTcpClient(host, port) as client:
          logger.info("Connected to Piper TTS")
          voice: SynthesizeVoice | None = None
          if voice_name is not None:
              voice = SynthesizeVoice(name=voice_name, language = None, speaker=None)
          synthesize = Synthesize(text=sentence, voice=voice)
          await client.write_event(synthesize.event())
          wav_writer: wave.Wave_write | None = None
          with io.BytesIO() as wav_io:
              while True:
                  event = await client.read_event()
                  if event is None:
                      logger.debug("Connection lost")
                      return

                  if AudioStop.is_type(event.type):
                      break

                  if AudioChunk.is_type(event.type):
                      chunk = AudioChunk.from_event(event)
                      if wav_writer is None:
                          wav_writer = wave.open(wav_io, "wb")
                          wav_writer.setframerate(chunk.rate)
                          wav_writer.setsampwidth(chunk.width)
                          wav_writer.setnchannels(chunk.channels)

                      wav_writer.writeframes(chunk.audio)
                      # yield chunk.audio  # // TO-DO: figure out why this does not work :(
                      
              if wav_writer is not None:
                  wav_writer.close()
                  
              wav_data = wav_io.getvalue() # yielding this also does not work (only the first sentence works), hmm...
              
              # Stupid workaround: convert WAV to MP3 using ffmpeg
              mp3_io = io.BytesIO()
              process = await asyncio.create_subprocess_exec(
                  'ffmpeg',
                  '-hide_banner',
                  '-loglevel', 'error',
                  '-i', 'pipe:0',         # input is WAV from stdin
                  '-f', 'mp3',            # output format
                  'pipe:1',               # send MP3 to stdout
                  stdin=asyncio.subprocess.PIPE,
                  stdout=asyncio.subprocess.PIPE,
                  stderr=asyncio.subprocess.PIPE
              )

              stdout, stderr = await process.communicate(input=wav_data)
              if process.returncode != 0:
                  logger.error(f"ffmpeg error: {stderr.decode()}")
                  return

              mp3_io.write(stdout)
              mp3_data = mp3_io.getvalue()

              # Now this finally works
              chunk_size = 1024
              for i in range(0, len(mp3_data), chunk_size):
                  yield mp3_data[i:i + chunk_size]
                  
    except (OSError, Exception) as e:
        logger.error(f"Piper TTS API error: {e}")
        yield b""  # Return an empty byte string on error

async def tts_stream(sentence: str, cfg: dict, logger):
    """
    Simple wrapper to route to whichever TTS engine is in config.
    """
    if cfg["main"]["tts_engine"] == "google_cloud":
        async for audio_chunk in tts_stream_google(sentence, credentials_path=cfg["google_cloud"]["credentials_path"], name=cfg["google_cloud"]["name"], language_code=cfg["google_cloud"]["language_code"], gender=cfg["google_cloud"]["gender"], logger=logger):
            yield audio_chunk
    elif cfg["main"]["tts_engine"]  == "openai":
        async for audio_chunk in tts_stream_openai(sentence, model=cfg["openai"]["model"], voice=cfg["openai"]["voice"], logger=logger):
            yield audio_chunk
    elif cfg["main"]["tts_engine"]  == "elevenlabs":
        async for audio_chunk in tts_stream_elevenlabs(sentence, model=cfg["elevenlabs"]["model"], voice=cfg["elevenlabs"]["voice"], api_key=cfg["elevenlabs"]["api_key"], logger=logger):
            yield audio_chunk
    elif cfg["main"]["tts_engine"]  == "piper":
        async for audio_chunk in tts_stream_piper(sentence, voice_name=cfg["piper"]["voice_name"], host=cfg["piper"]["host"], port=cfg["piper"]["port"], logger=logger):
            yield audio_chunk
    else:
        yield b""
