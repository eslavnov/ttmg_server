
import aiofiles
from asyncio import Event
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import json
import logging
import openai
import os

from helpers.audio_processing import create_persistent_flac_encoder, feed_encoder, stream_flac_from_audio_source
from helpers.tts_streaming import tts_stream_google, tts_stream_openai, tts_stream_elevenlabs, tts_stream
from helpers.sentence_parser import stream_sentence_generator, chunk_text

# Global config and client store
config = {}
store = {}

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(asctime)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger()

# Initialize API
app = FastAPI()

def store_get(client_id: str):
    """
    Returns store for a given client based on its id
    """
    global store
    return store[client_id] if client_id in store else {}

def store_put(client_id: str, data: dict):
    """
    Writes store for a given client based on its id
    """
    global store
    store[client_id] = data
    return store[client_id]

def get_client_events(client_id: str):
    """
    Returns (preload_event, play_event) for the given client_id,
    creating them if they don't yet exist in store[client_id].
    """
    client_store = store_get(client_id)
    if "preload_event" not in client_store:
        client_store["preload_event"] = Event()
    if "play_event" not in client_store:
        client_store["play_event"] = Event()
    store_put(client_id, client_store)
    return client_store["preload_event"], client_store["play_event"]
  
def config_get():
    """
    Returns global config
    """
    global config
    return config

def load_config():
    """
    Loads config, applies defaults for missing values
    """
    def merge_defaults(cfg: dict, defaults_cfg: dict):
        """Merges config with the defaults"""
        def merge(cfg, defaults_cfg):
            for key, value in defaults_cfg.items():
                if key not in cfg:
                    cfg[key] = value
                elif isinstance(value, dict):
                    cfg[key] = merge(cfg.get(key, {}), value)
            return cfg

        for key, value in defaults_cfg.items():
            if key in cfg:
                cfg[key] = merge(cfg[key], value)
            else:
                cfg[key] = value

        return cfg
  
    def validate_credentials(cfg:dict):
        try:
          if cfg["main"]["openai_api_key"]:
            openai.api_key = cfg["main"]["openai_api_key"]
        except KeyError as e:
          raise Exception("You need to provide an OpenAI API key in your configuration.json") from e
        try:
            if cfg["main"]["tts_engine"]=="google_cloud" and not cfg["google_cloud"]["credentials_path"]:
                raise Exception("You need to provide a Google Cloud credentials path in your configuration.json")
        except KeyError as e:
            raise Exception("You need to provide a Google Cloud credentials path in your configuration.json") from e
        try:
            if cfg["main"]["tts_engine"]=="elevenlabs" and not cfg["elevenlabs"]["api_key"]:
                raise Exception("You need to provide an ElevenLabs API key in your configuration.json")
        except KeyError as e:
            raise Exception("You need to provide an ElevenLabs API key in your configuration.json") from e
        return cfg
        
    # Load defaults and configuration from JSON file
    with open('defaults.json', 'r') as f:
      defaults = json.load(f)
    with open('configuration.json', 'r') as f:
      config = json.load(f)

    if config["main"]["tts_engine"] not in config:
      config[config["main"]["tts_engine"]] = {}

    return merge_defaults(validate_credentials(config), defaults)
            
def sentence_generator(text: str):
    """Yields sentences from text as they are detected."""
    sentence = ""
    for char in text:
        sentence += char
        if char in {'.', '!', '?'}:
            yield sentence.strip()
            sentence = ""
    if sentence.strip():
        yield sentence.strip()

async def llm_stream(cfg: str, prompt: str, llm_config: dict, client_id: str):
    """
    Streams responses from OpenAI's GPT-4. 
    If tool calls are in the response, calls them, waits for Home Assistant response and re-calls the API if needed.
    """
    messages = None
    client_store = store_get(client_id)
    if "messages" in client_store:
      messages = client_store["messages"]
      
    if messages is None:
        # Check if messages were provided in the llm config
        messages = (
            json.loads(llm_config["messages"])
            if llm_config and "messages" in llm_config
            else [
                {"role": "system", "content": cfg["main"]["llm_system_prompt"]}, {"role": "user", "content": prompt},
            ]
        )
        client_store["messages"] = messages
        store_put(client_id, client_store)

    client = openai.OpenAI(api_key=cfg["main"]["openai_api_key"])
    
    max_iterations = 10
    iteration_count = 0
    
    # The loop below:
    # 1. Calls LLM with the provided prompt
    # 2. If it returns tools calls, passes them to HASS and gets the response.
    # 3. Appends tools calls response to the message history and calls LLM again
    # This continues until the LLM returns text with no tool calls.
    # It will run once for simple requests and several times (up to 10) for tool calls.
    while iteration_count < max_iterations:
        tool_calls = {}  # Dictionary to store tool calls by index
        full_response = ""
        client_store = store_get(client_id)
        messages = client_store["messages"]
        logger.info("CALLING LLM")
        
        # Fail-safe in case we did not get tool_call response and try to issue a new command.
        # This might happen if TTMG Server or HASS crashes mid-response for some reason.
        # The happy flow never hits this code block.
        if "tool_calls" in messages[-2] and messages[-1]['role']=='user':
          logger.info("FAILSAFE TRIGGERED, FIXING HISTORY (DO NOT WORRY)")
          client_store["messages"].pop(-2)
          store_put(client_id, client_store)

        try:
            completion = client.chat.completions.create(
                model=llm_config["model"] if llm_config else cfg["main"]["llm_model"],
                messages=messages,
                tools=json.loads(llm_config["tools"]) if llm_config and "tools" in llm_config else None,
                temperature=llm_config["temperature"] if llm_config and "tools" in llm_config else cfg["main"]["temperature"],
                top_p= llm_config["top_p"] if llm_config and "tools" in llm_config else cfg["main"]["top_p"],
                max_completion_tokens= llm_config["max_completion_tokens"] if llm_config and "tools" in llm_config else cfg["main"]["max_completion_tokens"],
                stream=True,
            )
        except openai.OpenAIError as e:
            logger.error(f"OpenAI API error: {e}")
            return
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return

        # --- STREAM THE RESPONSE ---
        for chunk in completion:
            if chunk.choices:
                delta = chunk.choices[0].delta
                
                # Handle streaming text
                new_content = delta.content
                if new_content:
                    logger.info("Getting LLM response...")
                    full_response += new_content
                    yield new_content

                # Handle tool calls
                if delta.tool_calls:
                    for tool_call in delta.tool_calls:
                        index = tool_call.index
                        if index not in tool_calls:
                            tool_calls[index] = {
                                "id": tool_call.id,
                                "name": tool_call.function.name,
                                "arguments": "",
                            }
                        tool_calls[index]["arguments"] += tool_call.function.arguments

        # Add the full response as a single message (if it has any content)
        if full_response.strip():
            messages.append({"role": "assistant", "content": full_response.strip()})
            client_store = store_get(client_id)
            client_store["messages"] = messages
            store_put(client_id, client_store)

        # If there are tool calls, add them to messages
        if tool_calls:
            for tcall in tool_calls.values():
                try:
                    # Modify the tool calls to the required standard
                    tcall["function"] = {
                        "name": tcall["name"],
                        "arguments": tcall["arguments"],
                    }
                    tcall["type"] = "function"
                    del tcall["arguments"]
                    del tcall["name"]
                except json.JSONDecodeError:
                    logger.error(f"Error parsing JSON for tool (index={tcall}): {tcall['arguments']}")

            final_tool_calls = list(tool_calls.values())
            messages.append({"role": "assistant", "tool_calls": final_tool_calls})
            
            # Store the messages and tool_calls
            client_store = store_get(client_id)
            client_store["messages"] = messages
            client_store["tool_commands"] = final_tool_calls
            store_put(client_id, client_store)
            
            # We signal that we are done and unblock the /preload endpoint.
            # This will return tool_calls to TTMG Conversation integration for Home Assistant.
            logger.info("GOT TOOLS RESPONSE, RUNNING A PROMPT TO GENERATE SPEECH RESPONSE")
            preload_event, play_event = get_client_events(client_id)
            preload_event.set()
            
            # Now we wait for TTMG Conversation to call the tools
            # and append the response to the message history.
            await play_event.wait()
            play_event.clear()

            # With the tool calls response, we can re-run LLM to generate a nice output text.
        else:
            # No tool calls -> we can stop here
            break
        iteration_count += 1

async def audio_streamer(text: str, cfg: dict, client_id: str, llm_config=None, file_path: str = os.devnull):
    """
    Takes the user text, splits into sentences, calls TTS for each one,
    and yields the raw audio data in chunks. Also saves to 'file_path' (if desired).
    """
    if text is not None and text.strip() != "":
      async with aiofiles.open(file_path, 'wb') as f:
          async for sentence in stream_sentence_generator(chunk_text(text)):
              if sentence.strip():
                  logger.info(f"TTS {cfg['main']['tts_engine'].upper()} => {sentence}")
                  async for audio_chunk in tts_stream(sentence, cfg, logger):
                      await f.write(audio_chunk)
                      yield audio_chunk
    
async def prompt_audio_streamer(prompt: str, cfg: dict, client_id: str, llm_config: dict, file_path: str = os.devnull):
  """
  Runs LLM prompt and streams the response in real time.
  Takes the streaming response, splits into sentences, calls TTS for each one,
  and yields the raw MP3 data in chunks. Also saves to 'file_path' (if desired).
  """
  collected_text = ""
  async with aiofiles.open(file_path, 'wb') as f:
      async for sentence in stream_sentence_generator(llm_stream(cfg, prompt, llm_config, client_id)):
          if sentence.strip() !=".":
            logger.info(f"TTS {config['main']['tts_engine'].upper()}: {sentence}")
            async for audio_chunk in tts_stream(sentence, cfg, logger):
                await f.write(audio_chunk)
                yield audio_chunk
  
@app.post("/preload-text/{client_id}")
async def preload_text(client_id: str,  request: Request):
    """
    Accepts JSON {"text": "..."} and stores it globally so that
    /tts_say can use this text.
    """
    data = await request.json()
    text = data.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    client_store = store_get(client_id)
    client_store["preloaded_text"]= text
    store_put(client_id, client_store)
    
    logger.info(f"NEW PRELOADED TEXT: {client_store['preloaded_text']}")
    response_data = {
        "status": "ok",
        "msg": "Text preloaded successfully.",
    }
    return JSONResponse(content=response_data)
  
@app.post("/preload/{client_id}")
async def preload_llm_config(client_id: str, request: Request):
    """
    Accepts JSON {"messages", "tools", "model", "max_completion_tokens", "top_p" and "temperature"} and stores it globally so that
    /play/<filename>.flac can use this text instead of ?prompt=.
    """
    data = await request.json()
    messages = json.loads(data.get("messages", "[]"))
    tools = data.get("tools", "")
    max_completion_tokens = data.get("max_completion_tokens", "")
    top_p = data.get("top_p", "")
    temperature = data.get("temperature", "")
    model = data.get("model", "")
    if not (messages or tools or max_completion_tokens or top_p or temperature or model):
        raise HTTPException(status_code=400, detail='"messages", "tools", "model" and its params are required')

    logger.info(f"GOT USER MESSAGE: {messages[-1]['content']}")

    client_store = store_get(client_id)
    client_store["messages"] = messages
    client_store["preloaded_llm_config"] = {"messages": messages, "tools": tools, "max_completion_tokens":max_completion_tokens, "top_p":top_p, "temperature":temperature, "model": model  }
    store_put(client_id, client_store)

    # Now we have our llm config with tools and messages ready. 
    # We wait for the /play endpoint to trigger LLM pipeline with this config.
    # It will call preload_event.set() when it has the full response.
    preload_event, play_event = get_client_events(client_id)
    await preload_event.wait()
    preload_event.clear()
    
    # We have the full response now, so we return 
    # the updated messages history and the tool_calls to Home Assistant.
    # This will allow it to run the tools and append the results to the messages history.
    # Updated messages will come via the /write_history endpoint.
    client_store = store_get(client_id)
    tools_request = client_store["tool_commands"]
    client_store["tool_commands"] = None
    store_put(client_id, client_store)
    response_data = {
        "status": "ok",
        "msg": "Text preloaded successfully.",
        "tool_calls": tools_request,
        "messages": messages
    }
    return JSONResponse(content=response_data)

@app.get("/tts_say/{client_id}.flac")
async def tts(client_id: str, request: Request):
    """Processes a long text through TTS sentence by sentence and returns an audio stream in real time."""
    config = config_get()
    client_store = store_get(client_id)
    preloaded_text = client_store["preloaded_text"] if "preloaded_text" in client_store else None

    # Call a function to run LLM-TTS pipeline that returns a flac stream
    flac_stream = stream_flac_from_audio_source(audio_streamer, preloaded_text, config, client_id)

    return StreamingResponse(
        flac_stream,
        media_type="audio/flac",
        headers={"Content-Disposition": f'inline; filename="{client_id}.flac"'}     # Content-Disposition so the browser sees it as a .flac file
    )
      
@app.get("/play/{client_id}.{audio_format}")
async def play(client_id: str, audio_format: str, request: Request):
    """
    Endpoint for calling the LLM and streaming the TTS audio in flac or mp3 format.
    We use ?prompt= from the query string.
    Otherwise if llm config (tools + messages) was preloaded via /preload, we use that.
    """
    if audio_format not in ["flac", "mp3"]:
        raise Exception("Uknown audio format", audio_format)
    config = config_get()

    # Get llm config and preloaded text
    client_store = store_get(client_id)
    hass_store = store_get("ttmg_tts")
    preloaded_llm_config = client_store["preloaded_llm_config"] if "preloaded_llm_config" in client_store else None
    preloaded_text = hass_store["preloaded_text"] if "preloaded_text" in hass_store else None
    prompt = request.query_params.get("prompt", None)

    # Bypass the LLM and call the TTS engine directly if we have preloaded text
    # (for example, provided by TTMG TTS as a local agent response)
    if preloaded_text:
      # Clear the preloaded_text
      hass_store["preloaded_text"]= None
      store_put("ttmg_tts", hass_store)

      #  Call a function to run a TTS pipeline that returns an audio stream
      if audio_format == "flac":
        audio_stream = stream_flac_from_audio_source(audio_streamer, preloaded_text, config, client_id)
      elif audio_format == "mp3":
        audio_stream = audio_streamer(preloaded_text, config, client_id)

    # Handles the regular flow where we want to call the LLM 
    # and pipe the response into the TTS engine
    else:
      # Use prompt query param, otherwise use provided llm config
      if not preloaded_llm_config and not prompt:
          prompt ="Say you have received no prompt."
      llm_config =None if prompt else preloaded_llm_config
      # Clear the preloaded_llm config
      client_store["preloaded_llm_config"]= None
      store_put(client_id, client_store)
      
      # Call a function to run a LLM-TTS pipeline that returns an audio stream
      if audio_format == "flac":
        audio_stream = stream_flac_from_audio_source(prompt_audio_streamer, prompt, config, client_id, llm_config)
      elif audio_format == "mp3":
        audio_stream = prompt_audio_streamer(prompt, config, client_id, llm_config)

    return StreamingResponse(
        audio_stream,
        media_type="audio/"+audio_format,
        headers={"Content-Disposition": f'inline; filename="{client_id}.{audio_format}"'}     # Content-Disposition so the browser sees it correctly
    )
  
@app.get("/history/{client_id}")
async def get_history(client_id: str):
    """
    Returns the history of messages as a JSON response.
    Used by the Home Assistant integration to provide tool_calls responses.
    """
    client_store = store_get(client_id)
    return JSONResponse(content={"messages": client_store["messages"]})

@app.post("/write_history/{client_id}")
async def write_history(client_id: str, request: Request):
    """
    Writes the messages history.
    Used by the Home Assistant integration to provide tool_calls responses.
    """
    data = await request.json()
    messages = data.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="Messages are required")
      
    client_store = store_get(client_id)
    client_store["messages"] = messages
    store_put(client_id, client_store)
    preload_event, play_event = get_client_events(client_id)
    play_event.set()

    response_data = {"status": "ok", "msg": "Messages history updated."}
    return JSONResponse(content=response_data)
  
if __name__ == "__main__":
    import uvicorn
    config = load_config()
    uvicorn.run(app, host=config["main"]["host"], port=config["main"]["port"])
