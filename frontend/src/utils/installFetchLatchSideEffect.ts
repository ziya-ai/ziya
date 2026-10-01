/**
 * Side-effect import: wrap window.fetch before any other module can call it.
 *
 * ES imports are hoisted and evaluated in source order, so importing this
 * module first in index.tsx is the only way to run the install ahead of the
 * provider imports that follow.  A bare statement between imports would both
 * violate import/first and run too late.
 */
import { installFetchLatch } from './instanceIdentity';
installFetchLatch();
