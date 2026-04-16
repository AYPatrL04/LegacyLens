from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens.analyzers import CLikeAnalyzer, CobolAnalyzer, FortranAnalyzer


class AnalyzerTests(unittest.TestCase):
    def test_c_analyzer_detects_goto_union_and_bit_packing(self) -> None:
        code = """
union Word { int i; char b[4]; };
int main(void) {
    int flags = 0;
    flags = flags | 001;
    goto done;
done:
    return 0;
}
"""
        rule_ids = {finding.rule_id for finding in CLikeAnalyzer().analyze(code)}
        self.assertIn("c.union-overlay", rule_ids)
        self.assertIn("c.bit-packing", rule_ids)
        self.assertIn("c.goto", rule_ids)

    def test_fortran_analyzer_detects_common_and_arithmetic_if(self) -> None:
        code = """
      COMMON /ACCOUNT/ BALANCE, LIMIT
      IF (BALANCE) 10, 20, 30
      GO TO 99
"""
        rule_ids = {finding.rule_id for finding in FortranAnalyzer().analyze(code)}
        self.assertIn("fortran.common", rule_ids)
        self.assertIn("fortran.arithmetic-if", rule_ids)
        self.assertIn("fortran.goto", rule_ids)

    def test_cobol_analyzer_detects_perform_thru_and_redefines(self) -> None:
        code = """
           05 ACCOUNT-AREA REDEFINES RECORD-TYPE PIC X.
           PERFORM VALIDATE-INPUT THRU VALIDATE-EXIT.
           GO TO FINISH.
"""
        rule_ids = {finding.rule_id for finding in CobolAnalyzer().analyze(code)}
        self.assertIn("cobol.perform-thru", rule_ids)
        self.assertIn("cobol.redefines", rule_ids)
        self.assertIn("cobol.goto", rule_ids)


if __name__ == "__main__":
    unittest.main()
