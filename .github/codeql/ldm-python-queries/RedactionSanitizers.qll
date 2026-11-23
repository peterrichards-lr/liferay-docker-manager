/**
 * LDM-#2438: LDM's redaction functions, as sanitizers for the clear-text
 * storage and logging rules.
 *
 * `UI.redact` and `UI._redact_command_line` (ldm_core/ui.py) mask passwords,
 * tokens and keys before the trace log is written (LDM-#2437). The stock
 * queries cannot know that, so every write of a redacted string was reported
 * as clear-text storage or logging of sensitive data.
 *
 * The sanitizer is the RETURN VALUE of a call `UI.redact(...)` or
 * `UI._redact_command_line(...)`: taint stops there, and the argument itself
 * stays tainted, so logging the unredacted value is still reported.
 *
 * The match is tied to the real class: if either method is renamed or `UI`
 * moves out of ldm_core/ui.py, the sanitizer matches nothing and the alerts
 * come back, rather than silently trusting a call that no longer redacts.
 */

private import python
private import semmle.python.dataflow.new.DataFlow
private import semmle.python.security.dataflow.CleartextStorageCustomizations
private import semmle.python.security.dataflow.CleartextLoggingCustomizations

/** Holds if `name` is a redaction method of `ldm_core.ui.UI`. */
private predicate isRedactionMethodName(string name) {
  name = ["redact", "_redact_command_line"] and
  exists(Class ui, Function f |
    ui.getName() = "UI" and
    ui.getEnclosingModule().getFile().getRelativePath() = "ldm_core/ui.py" and
    f = ui.getAMethod() and
    f.getName() = name
  )
}

/** A call to `UI.redact(...)` or `UI._redact_command_line(...)`; its value is redacted. */
class LdmRedactionCall extends DataFlow::CallCfgNode {
  LdmRedactionCall() {
    exists(DataFlow::AttrRead read |
      read = this.getFunction() and
      isRedactionMethodName(read.getAttributeName()) and
      read.getObject().asExpr().(Name).getId() = "UI"
    )
  }
}

/** The result of an LDM redaction call, as a clear-text storage sanitizer. */
class LdmRedactionAsStorageSanitizer extends CleartextStorage::Sanitizer instanceof LdmRedactionCall
{ }

/** The result of an LDM redaction call, as a clear-text logging sanitizer. */
class LdmRedactionAsLoggingSanitizer extends CleartextLogging::Sanitizer instanceof LdmRedactionCall
{ }
