import React, { useState, useEffect, memo, useMemo, Suspense, useCallback } from 'react';
import { Button, message, Radio, Space, Spin, RadioChangeEvent } from 'antd';
import * as d3 from 'd3';
import { parseDiff, Diff, Hunk, tokenize, RenderToken, HunkProps } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { DiffLine } from './DiffLine';
