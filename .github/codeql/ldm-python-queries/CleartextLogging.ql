/**
 * @name Clear-text logging of sensitive information (LDM redaction-aware)
 * @description Logging sensitive information without encryption or hashing can
 *              expose it to an attacker. Values returned by LDM's `UI.redact` and
 *              `UI._redact_command_line` are treated as sanitized.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 7.5
 * @precision high
 * @id py/ldm-clear-text-logging-sensitive-data
 * @tags security
 *       external/cwe/cwe-312
 *       external/cwe/cwe-359
 *       external/cwe/cwe-532
 */

// LDM-#2438: the stock py/clear-text-logging-sensitive-data query, with two
// changes. It imports RedactionSanitizers, which extends the abstract
// `CleartextLogging::Sanitizer`. And it skips sinks under ldm_core/tests/:
// the redaction tests log a fake secret ON PURPOSE, to prove the trace log
// masks it (ldm_core/tests/test_trace_log_redaction.py), so an alert there
// reports the test working. The stock query is excluded by
// .github/codeql/codeql-config-python.yml, so this replaces it.
import python
private import semmle.python.dataflow.new.DataFlow
import CleartextLoggingFlow::PathGraph
import semmle.python.security.dataflow.CleartextLoggingQuery
import RedactionSanitizers

from
  CleartextLoggingFlow::PathNode source, CleartextLoggingFlow::PathNode sink, string classification
where
  CleartextLoggingFlow::flowPath(source, sink) and
  classification = source.getNode().(Source).getClassification() and
  not sink.getNode().getLocation().getFile().getRelativePath().matches("ldm\\_core/tests/%")
select sink.getNode(), source, sink, "This expression logs $@ as clear text.", source.getNode(),
  "sensitive data (" + classification + ")"
