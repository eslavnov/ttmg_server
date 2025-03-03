import icu
import regex

pattern = regex.compile(r'\b(\p{Lu}\w{0,3})\.(?!\w*\.)', regex.UNICODE)
        
def merge_adjacent_sentences(sentences, min_length=15):
    """
    Merges small sentences together, so "OK! I will do that!" 
    will remain as one sentence instead of ["OK!", "I will do that!"]
    """
    merged = []
    for s in sentences:
        s = s.strip()
        if not merged:
            merged.append(s)
        else:
            # If either the previous sentence or the current sentence is too short,
            # merge them.
            if len(merged[-1]) < min_length or len(s) < min_length:
                merged[-1] = merged[-1] + " " + s
            else:
                merged.append(s)
    return merged
  
def pre_process_text(text: str):
    """
    Finds occurrences of short tokens (1-4 alphanumeric characters)
    that start with a capital letter immediately followed by a period (.), 
    and replaces the period with a placeholder <DOT>. 
    
    It will match "Mr.", "Cpt.", "PhD.", initials like "A.", etc.
    
    The negative lookahead (?!\w*\.) ensures that we don't match if the
    token is part of a multi-dot abbreviation (e.g., U.S.A.).
    """
    return pattern.sub(lambda m: m.group(1) + "<DOT>", text)

def post_process_text(text: str):
    """
    Replaces the placeholder <DOT> back with an actual period.
    """
    return text.replace("<DOT>", ".")

def split_sentences(text: str, locale_str="en_US"):
    """
    Splits text into sentences using ICU's BreakIterator.
    """
    bi = icu.BreakIterator.createSentenceInstance(icu.Locale(locale_str))
    bi.setText(text)

    sentences = []
    start = bi.first()

    for end in bi:
        sentence = text[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = end
    return sentences

def process_buffer(buf):
      """
      Processes the given text buffer and returns a tuple:
        (list_of_complete_sentences, remaining_buffer)
      """
      possible_sentences = split_sentences(buf)
      if not possible_sentences:
          return [], buf

      if buf and buf[-1] in {'.', '?', '!'}:
          # All sentences are complete.
          sentences = [post_process_text(sentence.strip()) for sentence in possible_sentences]
          new_buf = ""
      else:
          # The last sentence might be incomplete; yield all but the last.
          sentences = [post_process_text(sentence.strip()) for sentence in possible_sentences[:-1]]
          new_buf = possible_sentences[-1] if possible_sentences else ""
      return sentences, new_buf
        
async def stream_sentence_generator(chunks, target_size=128, min_length=15):
    """
    Accumulates chunks until at least 'target_size' characters 
    have been buffered, then generates sentences from them
    with at least `min_length` characters.
    """
    buffer = "" # Our main buffer
    chunk_buffer = []  # Temporary buffer to collect chunks until we hit the target_size 
    current_size = 0  # Running total of the length of buffered chunks

    # Loop over each incoming chunk of text.
    async for chunk in chunks:
        chunk_buffer.append(chunk)
        current_size += len(chunk.encode('utf-8'))

        # Once we've accumulated at least target_size characters, process the buffered text.
        if current_size >= target_size:
            combined_text = ''.join(chunk_buffer)
            chunk_buffer.clear() 
            current_size = 0

            # Pre-process the combined text (workaround for titles, initials, etc.)
            preprocessed_chunk = pre_process_text(combined_text)
            buffer += preprocessed_chunk

            # Process the main buffer to split it into complete sentences,
            # and update the buffer with any leftover incomplete sentence.
            sentences, buffer = process_buffer(buffer)
            # Merge adjacent sentences if one of them is shorter than min_length.
            sentences = merge_adjacent_sentences(sentences, min_length)
            for sentence in sentences:
                yield sentence

    # After processing all full buffers, check if there are any leftover chunks.
    if chunk_buffer:
        combined_text = ''.join(chunk_buffer)
        preprocessed_chunk = pre_process_text(combined_text)
        buffer += preprocessed_chunk
        chunk_buffer.clear()
        sentences, buffer = process_buffer(buffer)
        # Yield each complete sentence obtained from the leftover text.
        for sentence in sentences:
            yield sentence

    # Finally, if any text remains in the main buffer (an incomplete sentence, perhaps),
    # post-process and yield it as the final sentence.
    if buffer:
        yield post_process_text(buffer.strip())

