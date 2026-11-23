/**
 * @name Clear-text storage of sensitive information (LDM redaction-aware)
 * @description Sensitive information stored without encryption or hashing can expose it to an
 *              attacker. Values returned by LDM's `UI.redact` and `UI._redact_command_line`
 *              are treated as sanitized.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 7.5
 * @precision high
 * @id py/ldm-clear-text-storage-sensitive-data
 * @tags security
 *       external/cwe/cwe-312
 *       external/cwe/cwe-315
 *       external/cwe/cwe-359
 */

// LDM-#2438: the stock py/clear-text-storage-sensitive-data query, verbatim
// apart from the import of RedactionSanitizers, which extends the abstract
// `CleartextStorage::Sanitizer`. The stock query is excluded by
// .github/codeql/codeql-config-python.yml, so this replaces it, not duplicates it.
import python
private import semmle.python.dataflow.new.DataFlow
import CleartextStorageFlow::PathGraph
import semmle.python.security.dataflow.CleartextStorageQuery
import RedactionSanitizers

from
  CleartextStorageFlow::PathNode source, CleartextStorageFlow::PathNode sink, string classification
where
  CleartextStorageFlow::flowPath(source, sink) and
  classification = source.getNode().(Source).getClassification()
select sink.getNode(), source, sink, "This expression stores $@ as clear text.", source.getNode(),
  "sensitive data (" + classification + ")"
