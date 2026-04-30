# Implementation Plan: #31DayMaySQL Challenge Website

## Overview

Create a new `index.html` file — a single-page HTML application using Tailwind CSS CDN that presents the #31DayMaySQL challenge. The design combines the editorial boldness of `31days-sql.html` (personality, progressive content format), the visual polish of the Creative Edition (glassmorphism, Space Grotesk, electric blue/cyan), and the functional structure of Portfolio Landing 1 (challenge matrix, code editor, results table).

**Reference files (DO NOT EDIT):**
- `31days-sql.html` — Content/data source (31 topics, Day 1 & 2 detailed content, week structure)
- `sql_challenge_creative_edition/code.html` — Visual design reference (glassmorphism style)
- `sql_challenge_portfolio_landing_1/code.html` — Functional structure reference (matrix, code editor)
- `the_design_system/DESIGN.md` — Design tokens and component specs

**Output file:** `index.html`

## Tasks

- [x] 1. Create base HTML structure with Tailwind config and design tokens
  - [x] 1.1 Set up index.html with Tailwind CDN, fonts, and custom config
    - Create `index.html` with HTML5 boilerplate, Tailwind CDN script, Google Fonts (Space Grotesk + Inter)
    - Configure Tailwind theme extending colors from The Design System (deep midnight navy, electric blue, cyan, violet)
    - Add custom CSS for glassmorphism panels, noise texture overlay, and glow effects
    - Set up the base body styles (dark background, font defaults, selection colors)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 8.1_

  - [x] 1.2 Build the page layout skeleton (header, main, footer)
    - Create header/hero section with the bold "#31DAYS OF TRICKY SQL" title using Space Grotesk display
    - Add eyebrow text "► May 2026 · vvizh. presents" and tagline
    - Add metadata row (Total Days: 31, Difficulty: Hard+, Dialect: PostgreSQL, Starts: 01 May)
    - Create footer with branding and challenge info
    - Use semantic HTML (header, main, footer, section, nav)
    - _Requirements: 1.1, 1.4, 7.1, 7.2, 7.3, 7.4, 8.4_

- [x] 2. Build Challenge Matrix and Filter System
  - [x] 2.1 Create the Challenge Matrix grid component
    - Add a 7-column grid showing 31 days with glassmorphism cell styling
    - Each cell shows day number with visual states: has-content (glowing border), pending (dimmed)
    - Color-code cells by difficulty: tricky (cyan), nasty (red/error), cursed (accent yellow-green)
    - Make cells with content clickable (opens modal)
    - Responsive: collapse to 4 columns on mobile
    - _Requirements: 1.2, 1.5_

  - [x] 2.2 Create the Filter Bar
    - Add filter buttons: All, Behavior, Pattern, Performance, Logic, ☠ Cursed Only
    - Style with glassmorphism/outline buttons, active state with primary fill
    - Wire filter logic to show/hide topic cards by category
    - Hide empty week sections when filter is active
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 3. Build Week Sections and Topic Cards
  - [x] 3.1 Create Week Section component with sticky header
    - Week number (display font, large, accent color), theme title, day count
    - Sticky header on scroll with background matching page bg
    - Border separators between weeks
    - _Requirements: 1.3_

  - [x] 3.2 Create Topic Card component
    - Day badge, difficulty badge (color-coded), format badge, topic name, description, tags
    - Cards with content get left accent border + pointer cursor + arrow indicator on hover
    - Glassmorphism hover effect (subtle background shift)
    - Staggered fade-in animation on initial render
    - Grid layout: auto-fill, minmax(340px, 1fr)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 8.2, 8.3_

- [x] 4. Build Content Modal system
  - [x] 4.1 Create modal overlay and structure
    - Fixed overlay with dark backdrop (88% opacity)
    - Modal container with glassmorphism styling, max-width 840px
    - Header: day badge, difficulty badge, title, description
    - Close button (✕) and click-outside-to-close
    - Fade-in/slide-up animation, body scroll lock
    - _Requirements: 4.1, 4.2, 4.8, 4.9, 10.1, 10.2_

  - [x] 4.2 Create Step Block rendering (code, result, insight, warning blocks)
    - Step number (display font), label, title, body text
    - Code block: dark recessed container, accent left border, syntax highlighting colors
    - Result block: monospace table format, cyan left border, highlighted values
    - Insight block: accent background tint, bold key takeaway
    - Warning block: error/red background tint, gotcha explanation
    - Separator between steps
    - _Requirements: 4.3, 4.4, 4.5, 4.6, 4.7, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [x] 5. Add JavaScript data and rendering logic
  - [x] 5.1 Port challenge data (weeks array with 31 topics)
    - Copy the `weeks` array from `31days-sql.html` with all 31 topics
    - Each topic: day, name, desc, difficulty, format, tags, category, hasContent
    - _Requirements: 5.1, 5.2_

  - [x] 5.2 Port Day 1 and Day 2 content (dayContent object)
    - Copy `dayContent` object with Step_Block arrays for Day 1 and Day 2
    - Maintain syntax highlighting span classes (kw, fn, str, cm, num, op)
    - _Requirements: 5.3_

  - [x] 5.3 Implement rendering functions
    - `renderMatrix()` — generates challenge matrix cells from data
    - `renderAll()` — generates week sections and topic cards
    - `openModal(day)` / `closeModal()` — modal open/close with content rendering
    - `filterTopics(category)` — filter logic with week section visibility
    - Keyboard support (Escape to close modal)
    - _Requirements: 5.4, 5.5, 10.1, 10.2, 10.3, 10.4, 10.5_

- [ ] 6. Add Day 3-7 content (Week 1 completion)
  - [ ] 6.1 Add Day 3: The Floating Point Trap in SUM()
  - [ ] 6.2 Add Day 4: DISTINCT Inside a Window Function Doesn't Exist
  - [ ] 6.3 Add Day 5: NULL in ORDER BY — Who Goes First?
  - [ ] 6.4 Add Day 6: Implicit Type Coercion That Kills Your Index
  - [ ] 6.5 Add Day 7: GROUP BY Ordinal Position — Feature or Time Bomb?

- [ ] 7. Add Day 8-14 content (Week 2: Rare Patterns)
  - [ ] 7.1 Add Day 8: The Gaps and Islands Problem
  - [ ] 7.2 Add Day 9: Relational Division
  - [ ] 7.3 Add Day 10: Pivot Without the PIVOT Keyword
  - [ ] 7.4 Add Day 11: Slowly Changing Dimensions in Raw SQL
  - [ ] 7.5 Add Day 12: Running Total With a Conditional Reset
  - [ ] 7.6 Add Day 13: Median and Percentile Without PERCENTILE_CONT
  - [ ] 7.7 Add Day 14: Bitwise Operations for a Permission Flag System

- [ ] 8. Add Day 15-21 content (Week 3: Recursive & Graph)
  - [ ] 8.1 Add Day 15: Recursive CTE — Graph Traversal in SQL
  - [ ] 8.2 Add Day 16: Simulating a Stack in Pure SQL
  - [ ] 8.3 Add Day 17: Topological Sort in SQL
  - [ ] 8.4 Add Day 18: Fibonacci and Mathematical Sequences via CTE
  - [ ] 8.5 Add Day 19: Solving Sudoku in SQL
  - [ ] 8.6 Add Day 20: Monte Carlo Simulation in SQL
  - [ ] 8.7 Add Day 21: Interval Overlaps — Scheduling Conflict Detection

- [ ] 9. Add Day 22-28 content (Week 4: Performance Traps)
  - [ ] 9.1 Add Day 22: NOT IN vs NOT EXISTS vs LEFT JOIN IS NULL
  - [ ] 9.2 Add Day 23: Function on Indexed Column — Index Is Dead
  - [ ] 9.3 Add Day 24: The Hidden Cost of Correlated Subqueries
  - [ ] 9.4 Add Day 25: Statistics Staleness
  - [ ] 9.5 Add Day 26: LATERAL JOIN
  - [ ] 9.6 Add Day 27: Partial Index
  - [ ] 9.7 Add Day 28: VACUUM, Table Bloat, and Dead Tuples

- [ ] 10. Add Day 29-31 content (Week 5: Final Boss)
  - [ ] 10.1 Add Day 29: SQL Injection via Second-Order Attack
  - [ ] 10.2 Add Day 30: Write-Skew and Phantom Reads
  - [ ] 10.3 Add Day 31: SQL Is Declarative — And That Has Consequences

- [ ] 11. Accessibility and responsive polish
  - [ ] 11.1 Add ARIA attributes and semantic improvements
    - role="dialog", aria-modal on modal; aria-labels on interactive elements; aria-hidden on decorative
    - _Requirements: 8.4_

  - [ ] 11.2 Verify contrast ratios and font fallbacks
    - Ensure muted text meets 4.5:1 ratio; proper font-family fallback stacks
    - _Requirements: 8.5, 8.6_

  - [ ] 11.3 Final responsive and animation pass
    - Verify mobile layout (600px breakpoint), modal full-width on mobile
    - Verify animations use only transform/opacity, complete within 300ms
    - _Requirements: 1.5, 8.2, 8.3, 10.1_

- [ ] 12. Final integration checkpoint
  - Verify all 31 days render correctly, filter works, modal opens for all content days, matrix reflects state

## Notes

- Output is a NEW file `index.html` — reference files are not modified
- Single HTML file with Tailwind CDN (no build step)
- Design: glassmorphism + editorial bold typography + functional challenge matrix
- Content for Days 3-31 follows the same Step_Block pattern as Day 1 and Day 2
- SQL code blocks use inline `<span>` with classes (kw, fn, str, cm, num, op) for syntax highlighting
- Each content task (6-10) creates dayContent entries following progressive format: setup → trap → explanation → fix → bonus
