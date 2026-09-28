//! Python's `datetime` arithmetic, to the microsecond (task P8.12).
//!
//! The Python router moves time in two different ways and both have to be reproduced:
//!
//! * the search holds elapsed time as a float of seconds and turns it into a step by float
//!   floor division ([`crate::forecast::SegmentDepths::step_after`]);
//! * route building, safe-until and the corridor guard add `timedelta(seconds=elapsed)` to an
//!   aware `datetime`, which **rounds to the microsecond, half to even**, and then take
//!   `(when - t0).total_seconds()`.
//!
//! A leg that arrives 0.4 microseconds before a step boundary is on one step in the first
//! reading and possibly the next in the second, so both are ported literally. Instants are held
//! as integer microseconds since the Unix epoch in UTC, which is exactly what CPython compares
//! and subtracts, plus the UTC offset they were written with, which is what `isoformat` prints.

use std::fmt;

/// An aware instant, as Python holds one: exact microseconds, plus the offset it prints with.
#[derive(Clone, Copy, Debug)]
pub struct DateTime {
    /// Microseconds since 1970-01-01T00:00:00Z.
    pub utc_us: i64,
    /// UTC offset in microseconds (Python allows sub-minute offsets; they print as `+HH:MM:SS`).
    pub offset_us: i64,
}

impl PartialEq for DateTime {
    fn eq(&self, other: &Self) -> bool {
        self.utc_us == other.utc_us
    }
}
impl Eq for DateTime {}
impl PartialOrd for DateTime {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for DateTime {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.utc_us.cmp(&other.utc_us)
    }
}

pub const US_PER_S: i64 = 1_000_000;
pub const IST_OFFSET_US: i64 = (5 * 3600 + 30 * 60) * US_PER_S;

/// Microseconds in `timedelta(seconds=secs)`, reproducing CPython's `delta_new`: the integer
/// part is exact, the fractional part is scaled by 1e6 in double arithmetic, and what is left
/// below a microsecond is rounded half to even against the parity of the running total.
pub fn timedelta_us(secs: f64) -> i64 {
    // accum("seconds", ...): modf, exact integer part times 1e6.
    let int_part = secs.trunc();
    let frac = secs - int_part;
    let mut x: i64 = (int_part as i64) * US_PER_S;
    let mut leftover = 0.0_f64;
    if frac != 0.0 {
        let scaled = 1e6_f64 * frac;
        let int2 = scaled.trunc();
        x += int2 as i64;
        leftover += scaled - int2;
    }
    if leftover != 0.0 {
        let mut whole = leftover.round(); // C round(): half away from zero
        if (whole - leftover).abs() == 0.5 {
            let x_is_odd = (x & 1) as f64;
            whole = 2.0 * ((leftover + x_is_odd) * 0.5).round() - x_is_odd;
        }
        x += whole as i64;
    }
    x
}

impl DateTime {
    /// `self + timedelta(seconds=secs)`.
    pub fn add_seconds(self, secs: f64) -> DateTime {
        DateTime {
            utc_us: self.utc_us + timedelta_us(secs),
            offset_us: self.offset_us,
        }
    }

    /// `(self - other).total_seconds()`.
    pub fn seconds_since(self, other: DateTime) -> f64 {
        (self.utc_us - other.utc_us) as f64 / 1e6
    }

    /// `datetime.isoformat()`.
    pub fn isoformat(&self) -> String {
        let local = self.utc_us + self.offset_us;
        let days = local.div_euclid(86_400 * US_PER_S);
        let rem = local.rem_euclid(86_400 * US_PER_S);
        let (y, m, d) = civil_from_days(days);
        let secs = rem / US_PER_S;
        let micro = rem % US_PER_S;
        let (hh, mm, ss) = (secs / 3600, (secs / 60) % 60, secs % 60);
        let mut out = format!("{y:04}-{m:02}-{d:02}T{hh:02}:{mm:02}:{ss:02}");
        if micro != 0 {
            out.push_str(&format!(".{micro:06}"));
        }
        out.push_str(&format_offset(self.offset_us));
        out
    }
}

impl fmt::Display for DateTime {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.isoformat())
    }
}

fn format_offset(offset_us: i64) -> String {
    let sign = if offset_us < 0 { '-' } else { '+' };
    let a = offset_us.abs();
    let micro = a % US_PER_S;
    let secs = a / US_PER_S;
    let (hh, mm, ss) = (secs / 3600, (secs / 60) % 60, secs % 60);
    let mut out = format!("{sign}{hh:02}:{mm:02}");
    if ss != 0 || micro != 0 {
        out.push_str(&format!(":{ss:02}"));
        if micro != 0 {
            out.push_str(&format!(".{micro:06}"));
        }
    }
    out
}

/// Days since 1970-01-01 for a proleptic Gregorian date (Howard Hinnant's algorithm).
pub fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// The result of parsing: an instant, or a naive wall time that has no offset.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Parsed {
    Aware(DateTime),
    /// Microseconds since the epoch as if the wall time were UTC; no offset was written.
    Naive(i64),
}

impl Parsed {
    /// The aware instant, attaching `offset_us` to a naive time as `replace(tzinfo=...)` does.
    pub fn assume(self, offset_us: i64) -> DateTime {
        match self {
            Parsed::Aware(dt) => dt,
            Parsed::Naive(wall) => DateTime {
                utc_us: wall - offset_us,
                offset_us,
            },
        }
    }
}

struct Cursor<'a> {
    b: &'a [u8],
    i: usize,
}

impl Cursor<'_> {
    fn digits(&mut self, n: usize) -> Option<i64> {
        if self.i + n > self.b.len() {
            return None;
        }
        let mut v = 0i64;
        for k in 0..n {
            let c = self.b[self.i + k];
            if !c.is_ascii_digit() {
                return None;
            }
            v = v * 10 + i64::from(c - b'0');
        }
        self.i += n;
        Some(v)
    }
    fn peek(&self) -> Option<u8> {
        self.b.get(self.i).copied()
    }
    fn eat(&mut self, c: u8) -> bool {
        if self.peek() == Some(c) {
            self.i += 1;
            true
        } else {
            false
        }
    }
    fn done(&self) -> bool {
        self.i == self.b.len()
    }
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 => 29,
        2 => 28,
        _ => 0,
    }
}

/// Fractional seconds: Python reads any number of digits and keeps six (truncating the rest).
fn fraction(c: &mut Cursor<'_>) -> Option<i64> {
    let start = c.i;
    while c.peek().is_some_and(|ch| ch.is_ascii_digit()) {
        c.i += 1;
    }
    let digits = &c.b[start..c.i];
    if digits.is_empty() {
        return None;
    }
    let mut micro = 0i64;
    for k in 0..6 {
        micro = micro * 10 + digits.get(k).map_or(0, |d| i64::from(d - b'0'));
    }
    Some(micro)
}

/// `(h, m, s, us)` from `HH[:MM[:SS[.ffffff]]]` or the compact `HH[MM[SS[.ffffff]]]`.
fn time_part(c: &mut Cursor<'_>) -> Option<(i64, i64, i64, i64)> {
    let h = c.digits(2)?;
    let (mut m, mut s, mut us) = (0, 0, 0);
    let extended = c.peek() == Some(b':');
    let more = |c: &Cursor<'_>| {
        if extended {
            c.peek() == Some(b':')
        } else {
            c.peek().is_some_and(|ch| ch.is_ascii_digit())
        }
    };
    if more(c) {
        if extended {
            c.eat(b':');
        }
        m = c.digits(2)?;
        if more(c) {
            if extended {
                c.eat(b':');
            }
            s = c.digits(2)?;
            if c.eat(b'.') || c.eat(b',') {
                us = fraction(c)?;
            }
        }
    }
    if h > 23 || m > 59 || s > 59 {
        return None;
    }
    Some((h, m, s, us))
}

/// `datetime.fromisoformat(text)` for the forms Python 3.11+ accepts in practice: a date
/// (`YYYY-MM-DD` or `YYYYMMDD`), optionally a separator (`T`, `t` or a space) and a time, and
/// optionally `Z` or a `+HH[:MM[:SS[.ffffff]]]` offset. `None` where Python raises `ValueError`.
pub fn fromisoformat(text: &str) -> Option<Parsed> {
    let b = text.as_bytes();
    let mut c = Cursor { b, i: 0 };
    let y = c.digits(4)?;
    let extended = c.eat(b'-');
    let mo = c.digits(2)?;
    if extended && !c.eat(b'-') {
        return None;
    }
    let d = c.digits(2)?;
    if !(1..=12).contains(&mo) || d < 1 || d > days_in_month(y, mo) || y < 1 {
        return None;
    }
    let (mut h, mut mi, mut s, mut us) = (0, 0, 0, 0);
    let mut offset: Option<i64> = None;
    if !c.done() {
        match c.peek() {
            Some(b'T' | b't' | b' ') => c.i += 1,
            _ => return None,
        }
        (h, mi, s, us) = time_part(&mut c)?;
        if !c.done() {
            match c.peek() {
                Some(b'Z' | b'z') => {
                    c.i += 1;
                    offset = Some(0);
                }
                Some(sign @ (b'+' | b'-')) => {
                    c.i += 1;
                    let (oh, om, os, ous) = time_part(&mut c)?;
                    let total = ((oh * 3600 + om * 60 + os) * US_PER_S) + ous;
                    offset = Some(if sign == b'-' { -total } else { total });
                }
                _ => return None,
            }
        }
        if !c.done() {
            return None;
        }
    }
    let wall = (days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60 + s) * US_PER_S + us;
    Some(match offset {
        Some(off) => Parsed::Aware(DateTime {
            utc_us: wall - off,
            offset_us: off,
        }),
        None => Parsed::Naive(wall),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn aware(text: &str) -> DateTime {
        match fromisoformat(text) {
            Some(Parsed::Aware(dt)) => dt,
            other => panic!("{text} parsed as {other:?}"),
        }
    }

    #[test]
    fn isoformat_round_trips() {
        for text in [
            "2019-07-02T08:40:00+05:30",
            "2019-07-02T08:40:00.123456+05:30",
            "2019-07-02T03:10:00+00:00",
            "1999-12-31T23:59:59.000001-03:00",
        ] {
            assert_eq!(aware(text).isoformat(), text);
        }
        assert_eq!(
            aware("2019-07-02T03:10:00Z").isoformat(),
            "2019-07-02T03:10:00+00:00"
        );
        assert_eq!(
            aware("2019-07-02T08:40:00.5+05:30").isoformat(),
            "2019-07-02T08:40:00.500000+05:30"
        );
        assert_eq!(
            aware("20190702T0840+0530").isoformat(),
            "2019-07-02T08:40:00+05:30"
        );
        assert!(matches!(
            fromisoformat("2019-07-02T08:40"),
            Some(Parsed::Naive(_))
        ));
        assert!(fromisoformat("08:40").is_none());
        assert!(fromisoformat("2019-02-30T00:00:00+05:30").is_none());
        assert!(fromisoformat("2019-07-02T08:40:00+05:30 ").is_none());
    }

    #[test]
    fn subtraction_is_in_utc() {
        let a = aware("2019-07-02T08:40:00+05:30");
        let b = aware("2019-07-02T03:10:00+00:00");
        assert_eq!(a.seconds_since(b), 0.0);
        assert_eq!(a, b);
    }

    #[test]
    fn timedelta_rounds_half_even_to_the_microsecond() {
        // Values checked against CPython 3.12: timedelta(seconds=x) // timedelta(microseconds=1)
        assert_eq!(timedelta_us(1.0), 1_000_000);
        assert_eq!(timedelta_us(0.000_000_5), 0); // 0.5 us, x=0 even -> 0
        assert_eq!(timedelta_us(0.000_001_5), 2); // 1.5 us, x=1 odd -> 2
        assert_eq!(timedelta_us(-1.25), -1_250_000);
        assert_eq!(timedelta_us(123.456_789_4), 123_456_789);
        assert_eq!(timedelta_us(1e-7), 0);
        assert_eq!(timedelta_us(2.5e-6), 2);
        assert_eq!(timedelta_us(1_234.000_002_5), 1_234_000_002);
        assert_eq!(timedelta_us(7.000_000_5), 7_000_000);
    }
}
