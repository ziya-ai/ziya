/**
 * Jest setup — auto-loaded by react-scripts before every test file
 * (CRA convention: src/setupTests.ts).  Registers @testing-library/jest-dom
 * custom matchers (toBeInTheDocument, toHaveAttribute, etc.) and ensures
 * the DOM is reset between tests so render-based suites don't leak state.
 */
import '@testing-library/jest-dom';
import { cleanup } from '@testing-library/react';

afterEach(() => cleanup());
