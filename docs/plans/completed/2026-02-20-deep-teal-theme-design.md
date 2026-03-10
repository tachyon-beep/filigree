# Deep Teal Color Theme Design

**Date:** 2026-02-20
**Status:** Approved
**Branch:** feat/multi-project-dashboard

## Context

The dashboard uses ~40+ hardcoded color values (hex in CSS, Tailwind utility classes in HTML, inline styles in JS). The light theme uses 10+ `!important` overrides. This design replaces the default "Tailwind Slate" palette with a distinctive "Deep Teal" palette and migrates all colors to CSS custom properties for maintainability.

## Palette: Deep Teal

### Dark Theme (default)

```
Surfaces:
  --surface-base:    #0B1215    body background
  --surface-raised:  #131E24    header, footer, cards, panels
  --surface-overlay: #1A2B34    inputs, dropdowns, modals
  --surface-hover:   #243A45    card hover, button hover

Borders:
  --border-default:  #1E3340    card borders, dividers
  --border-strong:   #2A4454    input focus, active elements

Text:
  --text-primary:    #E2EEF2    headings, card titles
  --text-secondary:  #8FAAB8    labels, metadata
  --text-muted:      #5A7D8C    placeholders, timestamps

Accent:
  --accent:          #38BDF8    buttons, active tabs, links
  --accent-hover:    #0EA5E9    button hover
  --accent-subtle:   #0C4A6E33  tinted backgrounds (20% opacity)

Scrollbar:
  --scrollbar-track: #131E24
  --scrollbar-thumb: #2A4454

Graph:
  --graph-text:      #E2EEF2    node labels
  --graph-outline:   #0B1215    text outline (matches base)
  --graph-edge:      #2A4454    dependency edges
```

### Light Theme

```
Surfaces:
  --surface-base:    #F0F6F8    teal-tinted off-white
  --surface-raised:  #FFFFFF    cards, header, footer
  --surface-overlay: #E8F1F4    inputs, dropdowns
  --surface-hover:   #DCE9EE    hover states

Borders:
  --border-default:  #C5D8E0    subtle borders
  --border-strong:   #9BBBC8    focus borders

Text:
  --text-primary:    #0F2027    near-black with teal tint
  --text-secondary:  #3D6070    medium contrast
  --text-muted:      #6B8D9C    low contrast

Accent:
  --accent:          #0284C7    sky-600 (darker for light bg)
  --accent-hover:    #0369A1    sky-700
  --accent-subtle:   #0284C733  tinted backgrounds

Scrollbar:
  --scrollbar-track: #E8F1F4
  --scrollbar-thumb: #B0C9D2

Graph:
  --graph-text:      #0F2027
  --graph-outline:   #F0F6F8
  --graph-edge:      #9BBBC8
```

### Status Category Colors (both themes)

```
  --status-open:     #64748B    slate gray (unchanged)
  --status-wip:      #38BDF8    sky-400 (matches accent)
  --status-done:     #7B919C    teal-tinted gray
```

### Semantic Colors (unchanged, both themes)

These are universal signals and do not change between themes:
```
  Ready:      #10B981  (emerald)
  Warning:    #F59E0B  (amber)
  Critical:   #EF4444  (red)
  High:       #F97316  (orange)
```

## Migration Strategy

### CSS Custom Properties

Define all colors on `:root` (dark default) and `[data-theme="light"]`:

```css
:root {
  --surface-base: #0B1215;
  --surface-raised: #131E24;
  /* ... etc */
}
[data-theme="light"] {
  --surface-base: #F0F6F8;
  --surface-raised: #FFFFFF;
  /* ... etc */
}
```

### What Changes

1. **`<style>` block** — Replace hardcoded hex with `var(--name)`. Remove `.light` override block (lines 56-64). Add `:root` and `[data-theme="light"]` variable blocks.

2. **HTML Tailwind classes** — Replace color-bearing classes:
   - `bg-slate-800` → `style="background:var(--surface-raised)"`
   - `bg-slate-700` → `style="background:var(--surface-overlay)"`
   - `text-slate-200` → `style="color:var(--text-primary)"`
   - `text-slate-400` → `style="color:var(--text-secondary)"`
   - `text-slate-500` → `style="color:var(--text-muted)"`
   - `border-slate-700` → `style="border-color:var(--border-default)"`
   - `border-slate-600` → `style="border-color:var(--border-strong)"`
   - `bg-blue-600` → `style="background:var(--accent)"`
   - `text-blue-400` → `style="color:var(--accent)"`
   - Hover states use companion CSS classes (not inline)

3. **JS constants** — Update `CATEGORY_COLORS` to use new palette values. `PRIORITY_COLORS` stays (semantic colors).

4. **JS render functions** — Update inline styles in `renderCard`, `renderClusterKanban`, `renderTypeKanban`, `openDetail`, plan view, etc.

5. **Cytoscape graph** — Update hardcoded hex in graph/workflow node/edge styles.

6. **Theme toggle** — Change from `classList.toggle('light')` to `dataset.theme = 'light'/'dark'`. LocalStorage key stays `filigree_theme`.

### What Stays the Same

- Semantic colors (red, amber, emerald, orange) — unchanged
- Font (JetBrains Mono) — unchanged
- Layout (all flexbox, spacing, sizing) — unchanged
- Animations (timing, keyframes structure) — unchanged, colors updated
- All JS logic — only color constants change
- Health score badge colors (emerald/amber/red based on score)
- Toast colors (emerald/red for success/error)

### Approach for Tailwind Classes

Rather than converting every Tailwind class to inline styles, we use a hybrid:

- **Static HTML elements** (header, footer, panels): Use CSS utility classes that reference custom properties. Add small helper classes like `.bg-surface-raised { background: var(--surface-raised); }` etc.
- **JS-generated HTML** (renderCard, openDetail, modals): Use inline `style="background:var(--surface-raised)"` since these are string-concatenated.
- **Semantic Tailwind classes** that don't change between themes: Keep as-is (e.g., `bg-emerald-900/50` for ready badges, `bg-red-900/50` for blocked badges).

## Implementation Order

1. Add CSS custom property definitions (`:root` and `[data-theme="light"]`)
2. Add helper utility classes (`.bg-surface-raised`, `.text-primary`, etc.)
3. Migrate `<style>` block hardcoded colors
4. Migrate static HTML elements (header, footer, kanban sub-header, views)
5. Migrate JS render functions (renderCard, renderClusterKanban, renderTypeKanban)
6. Migrate detail panel (openDetail)
7. Migrate modals, toasts, popovers, batch bar
8. Migrate Cytoscape graph styles
9. Update theme toggle mechanism
10. Remove old `.light` CSS overrides
11. Update JS constants (CATEGORY_COLORS)
12. Run CI and verify both themes

## Out of Scope

- Mobile-specific theme adjustments
- User-selectable accent colors
- Additional theme variants beyond dark/light
