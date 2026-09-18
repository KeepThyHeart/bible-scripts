/**
 * Command registry. Each command module exports
 * `{ name, usage, description, args, run(args, opts), render? }`; `args` is the
 * minimum number of positional arguments.
 *
 * GROUPS fixes the order commands appear in `help`.
 */

const GROUPS = {
  'General': [
    require('./modules'), require('./info'), require('./schema'),
    require('./tables'), require('./stats'), require('./sql'),
  ],
  'Bible': [
    require('./verse'), require('./search'), require('./chapters'), require('./verse-lookup'),
  ],
  'Commentary': [require('./commentary')],
  'Dictionary': [require('./define')],
  'Topical Index': [
    require('./topics'), require('./topic'), require('./topic-tree'),
    require('./topic-verses'), require('./verse-topics'),
  ],
  'Cross-references': [require('./xref')],
  'Book': [require('./sections'), require('./section')],
  'Devotional': [require('./devotional')],
};

const COMMANDS = {};
for (const commands of Object.values(GROUPS)) {
  for (const cmd of commands) COMMANDS[cmd.name] = cmd;
}

module.exports = { GROUPS, COMMANDS };
