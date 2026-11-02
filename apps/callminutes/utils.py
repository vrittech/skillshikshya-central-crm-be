import re

from django.core.exceptions import ValidationError


def parse_duration_to_seconds(duration_str):
    """
    Parse flexible duration formats to seconds.
    
    Accepted formats:
    - "4:19"      → 4 min 19 sec = 259s (MM:SS)
    - "4.19"      → 4 min 19 sec = 259s (dot as MM.SS)
    - "04:19"     → 4 min 19 sec
    - "4.19"      → 4 min 19 sec (dot as MM.SS)
    - "4m19s"     → explicit minutes/seconds
    - "4m"        → 4 minutes = 240s
    - "45s"       → 45 seconds
    - "259"       → 259 seconds
    - "4:19:30"   → hours:minutes:seconds
    - "4.5"       → 4 min 05 sec (or decimal minutes)
    
    Args:
        duration_str: String representation of duration
        
    Returns:
        int: Duration in seconds
        
    Raises:
        ValidationError: If format is invalid
    """
    if duration_str is None or duration_str == '':
        return 0
    
    s = str(duration_str).strip().lower()
    
    if not s:
        return 0
    
    # H:M:S or M:S format (overflow carries: "7:89" = 8min 29sec)
    if ':' in s:
        parts = s.split(':')
        if len(parts) == 2:
            try:
                minutes = int(parts[0])
                seconds = int(parts[1])
                return minutes * 60 + seconds
            except ValueError:
                raise ValidationError(f'Invalid MM:SS format: {duration_str}')
        elif len(parts) == 3:
            try:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = int(parts[2])
                return hours * 3600 + minutes * 60 + seconds
            except ValueError:
                raise ValidationError(f'Invalid H:M:S format: {duration_str}')
    
    # M.SS format (e.g., "4.19" = 4 min 19 sec, "4.5" = 4 min 05 sec).
    # Overflow carries: "7.89" = 8 min 29 sec.
    if '.' in s and ':' not in s:
        parts = s.split('.')
        if len(parts) == 2:
            try:
                minutes = int(parts[0])
                # Zero-pad/truncate the seconds part to 2 digits (left-padded).
                seconds_str = parts[1][:2].zfill(2)
                seconds = int(seconds_str)
                return minutes * 60 + seconds
            except ValueError:
                raise ValidationError(f'Invalid M.SS format: {duration_str}')
    
    # Minutes and seconds format (e.g., "4m19s", "4m 19s", "4 min 19 sec", "4 mins 19 secs")
    m_match = re.match(r'^(\d+)\s*(?:m|min|mins|minute|minutes)\s*(\d+)\s*(?:s|sec|secs|second|seconds)?$', s)
    if m_match:
        return int(m_match.group(1)) * 60 + int(m_match.group(2))

    # Minutes only (e.g., "4m", "4min", "4 mins", "4 minutes")
    m_match = re.match(r'^(\d+)\s*(?:m|min|mins|minute|minutes)$', s)
    if m_match:
        return int(m_match.group(1)) * 60

    # Seconds only (e.g., "45s", "45sec", "45 secs", "45 seconds")
    m_match = re.match(r'^(\d+)\s*(?:s|sec|secs|second|seconds)$', s)
    if m_match:
        return int(m_match.group(1))

    # Minutes only (e.g., "4m", "4min", "4 mins")
    if 'm' in s and 's' not in s:
        try:
            m_index = s.index('m')
            minutes_part = s[:m_index].strip()
            return int(minutes_part) * 60
        except (ValueError, IndexError):
            pass

    # Seconds only (e.g., "45s", "45sec")
    if 's' in s and 'm' not in s:
        try:
            s_index = s.index('s')
            seconds_part = s[:s_index].strip()
            return int(seconds_part)
        except (ValueError, IndexError):
            pass
    
    # Raw seconds (integer)
    try:
        seconds = int(s)
        if seconds < 0:
            raise ValidationError(f'Duration cannot be negative: {duration_str}')
        return seconds
    except ValueError:
        pass

    raise ValidationError(
        f'Invalid duration format: "{duration_str}". '
        'Accepted formats: "4:19", "4.19", "04:19", "4m19s", "259", "4:19:30"'
    )


def format_duration(seconds):
    """
    Format seconds as MM:SS string.
    
    Args:
        seconds: Integer seconds
        
    Returns:
        String in MM:SS format (e.g., "04:19")
    """
    if seconds is None or seconds < 0:
        return "00:00"
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"
