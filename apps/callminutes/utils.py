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
    - "259"       → 259 seconds
    - "4:19:30"   → hours:minutes:seconds
    - "4.5"       → 4.5 minutes = 270s (decimal minutes fallback)
    - "4m19s"     → explicit minutes/seconds
    
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

    if '-' in s:
        raise ValidationError(f'Duration cannot be negative: {duration_str}')
    
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
    
    # MmSs format (e.g., "4m19s"); overflow carries ("4m89s" = 5min 29sec)
    if 'm' in s and 's' in s:
        try:
            m_index = s.index('m')
            s_index = s.index('s')
            if m_index < s_index:
                minutes_part = s[:m_index]
                seconds_part = s[m_index+1:s_index]
                minutes = int(minutes_part) if minutes_part else 0
                seconds = int(seconds_part) if seconds_part else 0
                return minutes * 60 + seconds
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