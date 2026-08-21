# Code Review Checklist · 代码审查清单

## Feature: User Profile Page Performance Optimization

The current user profile page loads in 2.3 seconds on average. The goal
is to reduce this to under 500ms through targeted optimizations.

### Proposed Changes (Agent A: Developer)

```typescript
// components/UserProfile.tsx
// Optimization 1: Replace useEffect data fetching with React Query
// Optimization 2: Add virtualization for user activity list (>100 items)
// Optimization 3: Memoize expensive avatar processing
```

### Review Checklist (Agent B: Reviewer)

1. **Code Quality**: Are the changes following the project's ESLint and Prettier config?
2. **Performance**: Does the virtualization implementation handle edge cases (empty list, single item)?
3. **Test Coverage**: Are there unit tests for the memoized avatar processor?
4. **Accessibility**: Does the virtualized list maintain keyboard navigation?

### Review Feedback Template

Provide feedback in the following format:
- **Severity**: [BLOCKER / MAJOR / MINOR / NIT]
- **File**: path/to/file.ts
- **Line**: line number or range
- **Issue**: what's wrong
- **Suggestion**: how to fix it

### Post-Review Action
Agent A should address all BLOCKER and MAJOR feedback items before re-submission.
