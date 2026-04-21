import React from 'react';
import {render} from 'ink';

import {ShellApp} from './app.js';

const stdout = process.stdout;
let restored = false;

const restoreTerminal = () => {
	if (!stdout.isTTY || restored) {
		return;
	}
	restored = true;
	stdout.write('\u001b[?1049l\u001b[?25h');
};

if (stdout.isTTY) {
	stdout.write('\u001b[?1049h\u001b[2J\u001b[3J\u001b[H\u001b[?25l');
}

const instance = render(<ShellApp />);

instance.waitUntilExit().finally(() => {
	restoreTerminal();
});

process.on('exit', restoreTerminal);
process.on('SIGINT', restoreTerminal);
process.on('SIGTERM', restoreTerminal);