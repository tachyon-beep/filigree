// ---------------------------------------------------------------------------
// Shared state and constants for the Filigree dashboard.
// All modules import from here — single source of truth.
// ---------------------------------------------------------------------------

// --- Constants ---

export const CATEGORY_COLORS = { open: "#64748B", wip: "#38BDF8", done: "#7B919C" };

export const THEME_COLORS = {
  textPrimary: "#E2EEF2",
  textSecondary: "#8FAAB8",
  graphOutline: "#0B1215",
  graphEdge: "#2A4454",
  accent: "#38BDF8",
};

export const PRIORITY_COLORS = {
  0: "#EF4444",
  1: "#F97316",
  2: "#6B7280",
  3: "#D1D5DB",
  4: "#D1D5DB",
};

export const TYPE_ICONS = {
  bug: "\u{1F41B}",
  feature: "\u2728",
  task: "\u{1F4CB}",
  epic: "\u{1F4CA}",
  milestone: "\u{1F3AF}",
  step: "\u25B6",
};

export const TOUR_STEPS = [
  {
    el: "#btnKanban",
    text: "The dashboard has 5 views: Kanban (default), Graph, Metrics, Activity, and Workflow. Each shows your issues differently.",
    pos: "bottom",
  },
  {
    el: "#btnReady",
    text: "Ready issues have no blockers and can be worked on immediately. Toggle this to sort them first.",
    pos: "bottom",
  },
  {
    el: "#filterSearch",
    text: 'Search issues by title or ID. Press "/" anywhere to focus this field instantly.',
    pos: "bottom",
  },
  {
    el: "#healthBadge",
    text: "Health score (0\u201399) measures project flow. Click it for a detailed breakdown of what affects the score.",
    pos: "bottom",
  },
  {
    el: "#kanbanBoard",
    text: "Click any card to open its detail panel. Use j/k to navigate between cards with your keyboard.",
    pos: "top",
  },
  {
    el: null,
    text: 'Press "?" anytime to see all keyboard shortcuts. Look for small "?" icons next to features for contextual help. Happy tracking!',
    pos: "center",
  },
];

export const REFRESH_INTERVAL = 15000;

// --- Mutable application state ---

export const state = {
  // Core data
  allIssues: [],
  allDeps: [],
  issueMap: {},
  stats: null,

  // View state
  currentView: "kanban",
  kanbanMode: "standard",
  selectedIssue: null,
  selectedCards: new Set(),
  multiSelectMode: false,
  expandedEpics: new Set(),
  detailHistory: [],

  // Graph instances
  cy: null,
  workflowCy: null,

  // Filters
  readyFilter: true,
  blockedFilter: false,
  searchResults: null,
  _searchTimeout: null,

  // Type-filtered kanban
  typeTemplate: null,
  _typeFilterSeq: 0,

  // Drag-and-drop
  _dragIssueId: null,
  _dragTransitions: [],
  _transitionsLoaded: false,

  // Multi-project
  API_BASE: "/api",
  currentProjectKey: "",
  allProjects: [],

  // Health & critical path
  criticalPathIds: new Set(),
  criticalPathActive: false,
  impactScores: {},
  healthScore: null,

  // Change tracking
  previousIssueState: {},
  changedIds: new Set(),

  // Popover
  _activePopover: null,
};
