One fact per file. Example: `prefers-plain-language-answers.md`.
Frontmatter (`name`, `description`, `type`, `source`, `captured`, `visibility`) + free
markdown body. Types: `user`, `feedback`, `project`, `reference`.
`visibility:` is required — `shared` or `local`. `local` never leaves this machine.
Leaving it out does not publish the item: it is withheld, and the next publish refuses
until you say which you meant.
