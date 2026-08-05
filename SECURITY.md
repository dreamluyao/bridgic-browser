# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.x.x   | :white_check_mark: |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue, please report it responsibly.

### How to Report

1. **Do NOT** open a public GitHub issue for security vulnerabilities
2. Email the security issue to: zhangtl04@gmail.com
3. Include the following information:
   - Description of the vulnerability
   - Steps to reproduce the issue
   - Potential impact
   - Any suggested fixes (optional)

### What to Expect

- **Acknowledgment**: We will acknowledge receipt within 48 hours
- **Assessment**: We will assess the vulnerability within 7 days
- **Resolution**: For confirmed vulnerabilities, we aim to release a fix within 30 days
- **Disclosure**: We will coordinate with you on public disclosure timing

### Security Best Practices

When using bridgic-browser:

1. **User Data Protection**
   - Never store sensitive data (passwords, tokens) in logs
   - Use `is_secret=True` (CLI: `--secret`) for sensitive input fields on
     `input_text_by_ref`, `type_text`, and `fill_form` - or `"is_secret": true`
     on an individual `fill_form` field
   - **`is_secret` does not redact the arguments your agent framework records.**
     It covers what bridgic controls: the returned message, bridgic's logs, and
     error text. A framework that stores each tool call's arguments (step logs,
     traces, the next LLM prompt) is a separate sink outside this package - pass
     the call through `bridgic.browser.redact_tool_arguments(tool_name, args)`
     before recording it. See
     [docs/BROWSER_TOOLS_GUIDE.md](docs/BROWSER_TOOLS_GUIDE.md#secret-values-is_secret)
   - Remember two leak paths no flag can close: shell history for a CLI
     `--secret` invocation, and a page echoing the value back into a later
     snapshot
   - Be cautious with `user_data_dir` - it may contain sensitive browser data

2. **Network Security**
   - Be aware that stealth mode is designed to bypass bot detection, not for malicious purposes
   - Use appropriate proxies when needed
   - Monitor network requests for sensitive data leakage

3. **Code Execution**
   - Be careful with `evaluate_javascript` - validate all inputs
   - Do not execute untrusted JavaScript code
   - Review automation scripts before running them

4. **Dependencies**
   - Keep playwright and other dependencies updated
   - Review dependency security advisories regularly

## Acknowledgments

We appreciate security researchers who help keep bridgic-browser safe. Contributors who responsibly disclose vulnerabilities will be acknowledged (with permission) in our release notes.
