"""Tehran calendar days and Jalali calendar accounting periods."""
from datetime import date, timedelta

from core.jalali import gregorian_to_jalali, tehran_now
from core.utils import fa_digits

PERIODS = {'all': 'همهٔ دوره‌ها', 'day': 'روزانه', 'month': 'ماهانه',
           'quarter': '۳ ماهه', 'half': '۶ ماهه', 'year': 'سالانه'}


def _parts(day):
    return gregorian_to_jalali(day.year, day.month, day.day)


def _shift_month(first, count):
    for _ in range(abs(count)):
        probe = first + timedelta(days=32) if count > 0 else first - timedelta(days=1)
        first = probe - timedelta(days=_parts(probe)[2] - 1)
    return first


def period_bounds(period='all', anchor=None):
    if period not in PERIODS:
        raise ValueError('دورهٔ گزارش معتبر نیست.')
    try:
        day = date.fromisoformat(anchor) if anchor else tehran_now().date()
        if not 1900 <= day.year <= 2200:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('تاریخ گزارش معتبر نیست.') from None
    result = {'key': period, 'label': PERIODS[period], 'anchor': day.isoformat(),
              'start': None, 'end': None, 'previous': None, 'next': None,
              'range_label': 'همهٔ تاریخ‌ها'}
    if period == 'all':
        return result
    if period == 'day':
        start, end = day, day + timedelta(days=1)
        previous = start - timedelta(days=1)
    else:
        _, month, dom = _parts(day)
        months = {'month': 1, 'quarter': 3, 'half': 6, 'year': 12}[period]
        first = day - timedelta(days=dom - 1)
        start = _shift_month(first, -((month - 1) % months))
        end = _shift_month(start, months)
        previous = _shift_month(start, -months)
    def label(value):
        y, m, d = _parts(value)
        return fa_digits(f'{y:04}/{m:02}/{d:02}')
    result.update(start=start.isoformat() + ' 00:00:00', end=end.isoformat() + ' 00:00:00',
                  previous=previous.isoformat(), next=end.isoformat(),
                  range_label=label(start) + ' تا ' + label(end - timedelta(days=1)))
    return result


def period_filter(bounds):
    if bounds['start'] is None:
        return '', ()
    return ' AND created_at >= ? AND created_at < ?', (bounds['start'], bounds['end'])
