import { DiffTestSuite, DiffTestCase } from './diffTestTypes';

const createTestId = (category: string, type: string, index: number): string =>
    `${category}_${type}_${index.toString().padStart(3, '0')}`;

export const syntaxHighlightingTests: DiffTestCase[] = [
    {
        id: createTestId('display', 'syntax', 1),
        name: "Python Decorators and Type Hints",
        description: "Complex Python code with nested decorators and type hints",
        category: 'display-wellformed',
        type: 'syntax-highlighting',
        diff: `diff --git a/example.py b/example.py
index 1234567..89abcdef 100644
--- a/example.py
+++ b/example.py
@@ -1,5 +1,15 @@
from typing import Callable, TypeVar, Generic, ParamSpec

+P = ParamSpec('P')
+T = TypeVar('T')
+
+def complex_decorator[P, T](func: Callable[P, T]) -> Callable[P, T]:
+    @wraps(func)
+    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
+        return await func(*args, **kwargs)
+    return wrapper
+
class Example:
    def method(self):
        pass`,
        expectedResult: { success: true, appliedCleanly: true },
        tags: ['python', 'decorators', 'type-hints', 'pep-695']
    }
];

export const whitespaceTests: DiffTestCase[] = [
    {
        id: createTestId('display', 'whitespace', 1),
        name: "Mixed Indentation",
        description: "Diff with mixed tabs and spaces",
        category: 'display-wellformed',
        type: 'whitespace',
        diff: `diff --git a/example.py b/example.py
--- a/example.py
+++ b/example.py
@@ -1,5 +1,5 @@
def example():
-    first_level = True
-        second_level = True
+\tfirst_level = True
+\t    second_level = True
    return True`,
        expectedResult: { success: true },
        tags: ['whitespace', 'indentation', 'mixed-indent']
    }
];

export const lineEndingTests: DiffTestCase[] = [
    {
        id: createTestId('display', 'line-endings', 1),
        name: "Mixed Line Endings",
        description: "Diff with mixed CRLF and LF endings",
        category: 'display-mangled',
        type: 'line-endings',
        diff: `diff --git a/example.txt b/example.txt
--- a/example.txt
+++ b/example.txt
@@ -1,3 +1,3 @@
First line\r\n
-Second line\r\n
+Second line modified\n
Third line\r\n`,
        targetFile: 'example.txt',
        targetContent: "First line\r\nSecond line\r\nThird line\r\n",
        expectedResult: { 
            success: false,
            appliedCleanly: false,
            validationErrors: ['Line ending mismatch']
        },
        tags: ['line-endings', 'crlf', 'lf']
    }
];

export const contextTests: DiffTestCase[] = [
    {
        id: createTestId('apply', 'context', 1),
        name: "Missing Context Lines",
        description: "Diff with insufficient context lines",
        category: 'apply-mangled',
        type: 'context',
        diff: `diff --git a/example.js b/example.js
--- a/example.js
+++ b/example.js
@@ -5,2 +5,3 @@
-    console.log("old");
+    console.log("new");
+    console.log("added");`,
        targetFile: 'example.js',
        targetContent: `function example() {
   const x = 1;
   const y = 2;
   return x + y;
   console.log("old");
   return true;
}`,
        expectedResult: {
            success: false,
            appliedCleanly: false,
            validationErrors: ['Insufficient context']
        },
        tags: ['context', 'validation']
    }
];

export const diffTestSuites: DiffTestSuite[] = [
    {
        name: "Syntax Highlighting Tests",
        description: "Tests for syntax highlighting in different languages",
        cases: syntaxHighlightingTests
    },
    {
        name: "Whitespace Tests",
        description: "Tests for various whitespace scenarios",
        cases: whitespaceTests
    },
    {
        name: "Line Ending Tests",
        description: "Tests for different line ending combinations",
        cases: lineEndingTests
    },
    {
        name: "Context Tests",
        description: "Tests for diff context handling",
        cases: contextTests
    }
];
