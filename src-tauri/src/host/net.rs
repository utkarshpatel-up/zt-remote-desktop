//! Finding this computer's ZeroTier address(es).

use std::net::Ipv4Addr;

/// ZeroTier names its adapter differently on every OS:
///   Windows: "ZeroTier One [<network id>]"
///   Linux:   "zt" + 8 letters/digits, e.g. "zt5u4y3kc6"
///   macOS:   "feth" + digits (ZeroTier 1.6 and later), e.g. "feth2873"
pub fn is_zerotier(name: &str) -> bool {
    let n = name.to_lowercase();
    if n.contains("zerotier") {
        return true;
    }
    if let Some(rest) = n.strip_prefix("zt") {
        return rest.len() == 8 && rest.chars().all(|c| c.is_ascii_alphanumeric());
    }
    if let Some(rest) = n.strip_prefix("feth") {
        return !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit());
    }
    false
}

pub fn zerotier_ips() -> Vec<Ipv4Addr> {
    let mut ips = Vec::new();
    for i in if_addrs::get_if_addrs().unwrap_or_default() {
        if !is_zerotier(&i.name) {
            continue;
        }
        if let std::net::IpAddr::V4(ip) = i.ip() {
            if !ip.is_link_local() && !ip.is_loopback() && !ips.contains(&ip) {
                ips.push(ip);
            }
        }
    }
    ips
}

#[cfg(test)]
mod tests {
    use super::is_zerotier;
    #[test]
    fn names() {
        assert!(is_zerotier("ZeroTier One [8056c2e21c000001]"));
        assert!(is_zerotier("zt5u4y3kc6"));
        assert!(is_zerotier("feth2873"));
        assert!(!is_zerotier("en0"));
        assert!(!is_zerotier("zt123"));
        assert!(!is_zerotier("fethx"));
        assert!(!is_zerotier("Wi-Fi"));
    }
}
