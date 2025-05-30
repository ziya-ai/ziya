# Test file for characterizing diff apply behavior - Modified
# Adding a line that looks like a diff: --- a/test.py
# Another line that looks like a diff: +++ b/test.py

def marker_section_1():
    """Section 1: Lines 1-10"""
    line_1 = "MARKER 1"

    # Removing several lines and replacing with different indentation
      line_2 = "INDENTED MARKER 2"
        line_3 = "MORE INDENTED MARKER 3"
    line_4_5 = "COMBINED MARKERS 4 AND 5"
    return locals()

def marker_section_2():
    """Section 2: Lines 11-20"""
    line_11 = "MARKER 11"
    line_12 = "MARKER 12"
    line_13 = "MARKER 13"
    line_14 = "MARKER 14"
    line_15 = "MARKER 15"
    return locals()

def marker_section_3():
    """Section 3: Lines 21-30"""
    # Adding a line that contains diff-like content
    line_21 = "--- MARKER 21 ---"
    line_22 = "+++ MARKER 22 +++"
    line_23 = "MARKER 23"
    line_24 = "MARKER 24"
    line_25 = "MARKER 25"
    result = locals()
    return result  # Modified return

def marker_section_4():
    """Section 4: Lines 31-40"""
    line_31 = "MARKER 31"
    line_32 = "MARKER 32"
    line_33 = "MARKER 33"
    line_34 = "MARKER 34"
    # Replacing line 35 with multiple lines
    line_35a = "SPLIT MARKER 35 - PART A"
    line_35b = "SPLIT MARKER 35 - PART B"
    return locals()

def marker_section_5():
    """Section 5: Lines 41-50"""
    line_41 = "MARKER 41"
    line_42 = "MARKER 42"
    line_43 = "MARKER 43"
    line_44 = "MARKER 44"
    line_45 = "MARKER 45"
    return locals()
