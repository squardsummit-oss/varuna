//! Python's number semantics, where the Rust defaults differ and the answer would change.
//!
//! "The same answers" (task P8.12) is a bit-for-bit claim, so every place the Python router
//! leans on a CPython behaviour that Rust does not share is reproduced here from the CPython
//! source rather than approximated:
//!
//! * `a // b` on floats is not `(a / b).floor()`: CPython computes it from `fmod` so that
//!   `a == b * (a // b) + a % b` holds, which differs from the naive quotient near integers.
//! * `round(x, n)` and `f"{x:.nf}"` round the exact binary value half-to-even.
//! * `sum()` of floats has used Neumaier compensated summation since Python 3.12.
//! * `repr(float)` switches to exponent notation outside `1e-4 <= |x| < 1e16`.

use serde_json::Value;

/// `vx // wx` for Python floats (CPython `float_floor_div`, via `_float_div_mod`).
pub fn floordiv(vx: f64, wx: f64) -> f64 {
    let mut modv = vx % wx; // Rust's `%` on f64 is C's fmod.
    let mut div = (vx - modv) / wx;
    if modv != 0.0 {
        if (wx < 0.0) != (modv < 0.0) {
            modv += wx;
            div -= 1.0;
        }
    } else {
        modv = 0.0_f64.copysign(wx);
    }
    let _ = modv;
    if div != 0.0 {
        let mut floordiv = div.floor();
        if div - floordiv > 0.5 {
            floordiv += 1.0;
        }
        floordiv
    } else {
        0.0_f64.copysign(vx / wx)
    }
}

/// Python's `int(x)` for a float that is known to be finite: truncation toward zero.
pub fn trunc_i64(x: f64) -> i64 {
    x.trunc() as i64
}

/// `round(x, ndigits)` for a Python float: the exact binary value rounded half-to-even to
/// `ndigits` decimal places, then parsed back to the nearest double.
///
/// Rust's `{:.*}` formatting is exact and rounds ties to even, which is what CPython's
/// `double_round` gets from `_Py_dg_dtoa` mode 3, so formatting and parsing back reproduces it.
/// A unit test pins the tie cases (`round(0.125, 2) == 0.12`, `round(2.5, 0) == 2.0`).
pub fn round_n(x: f64, ndigits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let text = format!("{:.*}", ndigits, x);
    let back: f64 = text.parse().unwrap_or(x);
    // round() keeps the sign of zero: round(-0.04, 1) is -0.0.
    if back == 0.0 {
        return 0.0_f64.copysign(x);
    }
    back
}

/// `f"{x:.{n}f}"`.
pub fn format_fixed(x: f64, ndigits: usize) -> String {
    format!("{:.*}", ndigits, x)
}

/// `sum(values)` over Python floats, as CPython 3.12+ computes it: the first item is taken
/// exactly (it is added to the integer start 0), then Neumaier's compensated summation.
pub fn py_sum(values: &[f64]) -> f64 {
    let mut iter = values.iter();
    let Some(&first) = iter.next() else {
        return 0.0;
    };
    let mut f_result = first;
    let mut c = 0.0_f64;
    for &x in iter {
        let t = f_result + x;
        if f_result.abs() >= x.abs() {
            c += (f_result - t) + x;
        } else {
            c += (x - t) + f_result;
        }
        f_result = t;
    }
    if c != 0.0 && c.is_finite() {
        f_result += c;
    }
    f_result
}

/// `repr(x)` for a Python float.
pub fn repr_float(x: f64) -> String {
    if x.is_nan() {
        return "nan".to_string();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    if x == 0.0 {
        return if x.is_sign_negative() {
            "-0.0".into()
        } else {
            "0.0".into()
        };
    }
    // `{:e}` gives the shortest round-tripping digits, e.g. "7.284e1" or "-1e-5".
    let sci = format!("{:e}", x);
    let (mantissa, exp) = sci
        .split_once('e')
        .expect("LowerExp always has an exponent");
    let exp: i32 = exp.parse().expect("exponent is an integer");
    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(|c| c.is_ascii_digit()).collect();
    let sign = if negative { "-" } else { "" };
    if !(-4..16).contains(&exp) {
        let mut out = String::from(sign);
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push('e');
        out.push(if exp < 0 { '-' } else { '+' });
        out.push_str(&format!("{:02}", exp.abs()));
        return out;
    }
    let n = digits.len() as i32;
    let mut out = String::from(sign);
    if exp < 0 {
        out.push_str("0.");
        for _ in 0..(-exp - 1) {
            out.push('0');
        }
        out.push_str(&digits);
    } else if exp + 1 >= n {
        out.push_str(&digits);
        for _ in 0..(exp + 1 - n) {
            out.push('0');
        }
        out.push_str(".0");
    } else {
        let split = (exp + 1) as usize;
        out.push_str(&digits[..split]);
        out.push('.');
        out.push_str(&digits[split..]);
    }
    out
}

/// `repr()` of a Python `str`, close enough for the error messages that quote a value.
pub fn repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::new();
    out.push(quote);
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn repr_number(n: &serde_json::Number) -> String {
    if let Some(i) = n.as_i64() {
        return i.to_string();
    }
    if let Some(u) = n.as_u64() {
        return u.to_string();
    }
    repr_float(n.as_f64().unwrap_or(f64::NAN))
}

/// `repr()` of a JSON value as Python's `json.loads` would have built it.
pub fn repr_value(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => repr_number(n),
        Value::String(s) => repr_str(s),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(repr_value).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", repr_str(k), repr_value(v)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

/// `str()` of a JSON value as Python's `json.loads` would have built it.
pub fn str_value(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => repr_value(other),
    }
}

/// Python truthiness of a JSON value.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `float(v)` for a JSON value; `None` where Python raises `TypeError` or `ValueError`.
pub fn float_value(v: &Value) -> Option<f64> {
    match v {
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        Value::Number(n) => n.as_f64(),
        Value::String(s) => parse_py_float(s),
        _ => None,
    }
}

/// `float(str)`: surrounding whitespace, `inf`/`nan` in any case, and underscores between digits.
pub fn parse_py_float(s: &str) -> Option<f64> {
    let t = s.trim();
    if t.is_empty() {
        return None;
    }
    let lower = t.to_ascii_lowercase();
    let body = lower.trim_start_matches(['+', '-']);
    let negative = lower.starts_with('-');
    match body {
        "inf" | "infinity" => {
            return Some(if negative {
                f64::NEG_INFINITY
            } else {
                f64::INFINITY
            })
        }
        "nan" => return Some(f64::NAN),
        _ => {}
    }
    // Underscores are allowed only between two digits.
    let bytes = t.as_bytes();
    for (i, b) in bytes.iter().enumerate() {
        if *b == b'_' {
            let ok = i > 0
                && i + 1 < bytes.len()
                && bytes[i - 1].is_ascii_digit()
                && bytes[i + 1].is_ascii_digit();
            if !ok {
                return None;
            }
        }
    }
    let cleaned: String = t.chars().filter(|c| *c != '_').collect();
    if cleaned
        .chars()
        .any(|c| !(c.is_ascii_digit() || matches!(c, '.' | 'e' | 'E' | '+' | '-')))
    {
        return None;
    }
    cleaned.parse::<f64>().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floordiv_matches_cpython() {
        assert_eq!(floordiv(7.0, 5.0), 1.0);
        assert_eq!(floordiv(-7.0, 5.0), -2.0);
        assert_eq!(floordiv(299.999_999_999, 300.0), 0.0);
        assert_eq!(floordiv(600.0, 300.0), 2.0);
        assert_eq!(floordiv(-0.0, 5.0), -0.0);
        assert!(floordiv(-0.0, 5.0).is_sign_negative());
        // A case where (a / b).floor() and a // b disagree in Python: 1 // 0.1 == 9.0.
        assert_eq!(floordiv(1.0, 0.1), 9.0);
        assert_ne!((1.0_f64 / 0.1).floor(), 9.0);
    }

    #[test]
    fn round_is_half_even_on_the_exact_value() {
        assert_eq!(round_n(0.125, 2), 0.12);
        assert_eq!(round_n(0.375, 2), 0.38);
        assert_eq!(round_n(2.5, 0), 2.0);
        assert_eq!(round_n(3.5, 0), 4.0);
        assert_eq!(round_n(0.35, 1), 0.3); // 0.35 is 0.34999... in binary
        assert_eq!(round_n(72.845_678_9, 6), 72.845_679);
        assert!(round_n(-0.04, 1).is_sign_negative());
        assert_eq!(format_fixed(0.2, 2), "0.20");
        assert_eq!(format_fixed(60.0, 0), "60");
        assert_eq!(format_fixed(0.125, 2), "0.12");
    }

    #[test]
    fn sum_is_neumaier() {
        // sum([1e100, 1.0, -1e100, 1.0]) is 2.0 in Python 3.12; a plain fold gives 1.0.
        assert_eq!(py_sum(&[1e100, 1.0, -1e100, 1.0]), 2.0);
        assert_eq!(py_sum(&[0.1, 0.2, 0.3]), 0.6);
        assert_eq!(py_sum(&[]), 0.0);
    }

    #[test]
    fn repr_float_matches_python() {
        assert_eq!(repr_float(72.84), "72.84");
        assert_eq!(repr_float(100.0), "100.0");
        assert_eq!(repr_float(1e16), "1e+16");
        assert_eq!(repr_float(1.5e-5), "1.5e-05");
        assert_eq!(repr_float(0.0001), "0.0001");
        assert_eq!(repr_float(-2.5), "-2.5");
        assert_eq!(repr_float(123456789012345.6), "123456789012345.6");
        assert_eq!(repr_float(f64::NAN), "nan");
    }

    #[test]
    fn values_print_as_python_would() {
        let v: Value = serde_json::json!(["a", 1, 1.0, null, true, {"k": "it's"}]);
        assert_eq!(repr_value(&v), "['a', 1, 1.0, None, True, {'k': \"it's\"}]");
        assert_eq!(str_value(&serde_json::json!(12)), "12");
        assert_eq!(str_value(&Value::Null), "None");
        assert_eq!(parse_py_float(" 1_000.5 "), Some(1000.5));
        assert_eq!(parse_py_float("abc"), None);
        assert!(parse_py_float("NaN").unwrap().is_nan());
    }
}
