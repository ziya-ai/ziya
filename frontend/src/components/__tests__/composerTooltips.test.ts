import * as fs from 'fs';
import * as path from 'path';

const SOURCE = fs.readFileSync(
  path.join(__dirname, '..', 'SendChatContainer.tsx'),
  'utf-8',
);

describe('composer action tooltips', () => {
  const actionSection = SOURCE.slice(
    SOURCE.indexOf('{/* Attach file button'),
    SOURCE.indexOf('{/* Stop button when streaming'),
  );

  test('uses the same Ant Design Tooltip component as the top toolbar', () => {
    expect(SOURCE).toContain(
      "import { Button, message, Tooltip } from 'antd';",
    );
    expect(actionSection.match(/<Tooltip/g)).toHaveLength(2);
  });

  test('provides styled tooltips for attachment and microphone actions', () => {
    expect(actionSection).toContain(
      '<Tooltip title={supportsVision ? "Attach image or document" : "Attach document"}>',
    );
    expect(actionSection).toContain("'Stop recording and transcribe'");
    expect(actionSection).toContain("'Record voice input locally'");
  });

  test('wraps disabled buttons so their tooltips remain hoverable', () => {
    expect(actionSection.match(/<span style=\{\{ display: 'inline-flex' \}\}>/g)).toHaveLength(2);
  });
});
