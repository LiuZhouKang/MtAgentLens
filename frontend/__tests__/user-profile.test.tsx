// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  UserProfile,
  processAvatar,
  getErrorMessage,
} from '@/components/ui/user-profile';

// ---- Mock state for @tanstack/react-virtual ----
// Mutable ref pattern: each test updates virtualItems before rendering.
let virtualItems: Array<{
  key: string;
  index: number;
  start: number;
  size: number;
}> = [];
let virtualTotalSize = 0;
const mockScrollToIndex = vi.fn();

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: vi.fn(() => ({
    getVirtualItems: () => virtualItems,
    getTotalSize: () => virtualTotalSize,
    scrollToIndex: mockScrollToIndex,
  })),
}));

// ---- Test helpers ----
function resetVirtualMock() {
  virtualItems = [];
  virtualTotalSize = 0;
  mockScrollToIndex.mockClear();
}

function setVirtualItems(count: number) {
  virtualItems = Array.from({ length: count }, (_, i) => ({
    key: `v-${i}`,
    index: i,
    start: i * 80,
    size: 80,
  }));
  virtualTotalSize = count * 80;
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

function mockFetchUser(user: Record<string, unknown> | null, activities: Array<Record<string, unknown>>) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
    new Response(JSON.stringify({ user, activities }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

function mockFetchReject(error: Error) {
  vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(error);
}

// ---- processAvatar unit tests ----
describe('processAvatar', () => {
  it('returns a CDN URL with default size=200', () => {
    expect(processAvatar('avatar_123')).toBe(
      'https://img.example.com/avatar_123?w=200&q=85&fit=crop',
    );
  });

  it('returns a CDN URL with custom size', () => {
    expect(processAvatar('avatar_456', 400)).toBe(
      'https://img.example.com/avatar_456?w=400&q=85&fit=crop',
    );
  });

  it('returns empty string for empty avatar input', () => {
    expect(processAvatar('')).toBe('');
  });

  it('returns empty string for whitespace-only avatar', () => {
    expect(processAvatar('   ')).toBe('');
  });
});

// ---- getErrorMessage unit tests ----
describe('getErrorMessage', () => {
  it('extracts message from Error instance', () => {
    expect(getErrorMessage(new Error('Network error'))).toBe('Network error');
  });

  it('returns the string itself for string errors', () => {
    expect(getErrorMessage('Timeout')).toBe('Timeout');
  });

  it('extracts message from object with .message property', () => {
    expect(getErrorMessage({ message: 'Custom error' })).toBe('Custom error');
  });

  it('returns fallback for null/undefined/number', () => {
    expect(getErrorMessage(null)).toBe('An unexpected error occurred');
    expect(getErrorMessage(undefined)).toBe('An unexpected error occurred');
    expect(getErrorMessage(42)).toBe('An unexpected error occurred');
  });

  it('handles TypeError (common for network failures)', () => {
    expect(getErrorMessage(new TypeError('Failed to fetch'))).toBe(
      'Failed to fetch',
    );
  });

  it('returns fallback for object with non-string .message', () => {
    expect(getErrorMessage({ message: 123 })).toBe('An unexpected error occurred');
  });

  it('returns fallback for empty object', () => {
    expect(getErrorMessage({})).toBe('An unexpected error occurred');
  });
});

// ---- UserProfile component tests ----
describe('UserProfile', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    resetVirtualMock();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders loading skeleton on mount', () => {
    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });
    // Skeleton uses aria-label — no sr-only span needed
    expect(
      screen.getByRole('status', { name: 'Loading user profile' }),
    ).toBeDefined();
  });

  it('renders error state when query fails', async () => {
    mockFetchReject(new Error('Network error'));

    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });

    const errorEl = await screen.findByText('Network error');
    expect(errorEl).toBeDefined();
    expect(errorEl.className).toContain('text-red-500');
    expect(errorEl.getAttribute('role')).toBe('alert');
  });

  it('renders not-found state when user is null', async () => {
    mockFetchUser(null, []);

    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });

    expect(await screen.findByText('User not found')).toBeDefined();
  });

  it('renders empty activity state when user has no activities', async () => {
    mockFetchUser(
      { id: '1', name: 'Alice', email: 'alice@test.com', avatar: 'a1', bio: 'Hello' },
      [],
    );

    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });

    expect(await screen.findByText('No activities yet.')).toBeDefined();
    expect(screen.getByText('Activity History (0)')).toBeDefined();
  });

  it('renders user info and activity count', async () => {
    const activities = Array.from({ length: 3 }, (_, i) => ({
      id: `act-${i}`,
      title: `Activity ${i}`,
      description: `Description ${i}`,
      timestamp: `2026-06-0${i + 1}`,
      type: 'test',
    }));

    mockFetchUser(
      { id: '1', name: 'Bob', email: 'bob@test.com', avatar: 'b1', bio: 'Dev' },
      activities,
    );

    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });

    expect(await screen.findByText('Bob')).toBeDefined();
    expect(screen.getByText('bob@test.com')).toBeDefined();
    expect(screen.getByText('Dev')).toBeDefined();
    expect(screen.getByText('Activity History (3)')).toBeDefined();
  });

  it('includes userId in fetch error message', async () => {
    mockFetchReject(new Error('Failed to fetch profile for user bad-id'));

    render(<UserProfile userId="bad-id" />, { wrapper: createWrapper() });

    expect(
      await screen.findByText('Failed to fetch profile for user bad-id'),
    ).toBeDefined();
  });
});

// ---- ActivityList keyboard navigation tests ----
describe('ActivityList keyboard navigation', () => {
  const mockActivities = Array.from({ length: 5 }, (_, i) => ({
    id: `act-${i}`,
    title: `Activity ${i}`,
    description: `Description ${i}`,
    timestamp: `2026-06-0${i + 1}`,
    type: 'test',
  }));

  const singleActivity = [
    {
      id: 'act-single',
      title: 'Single Activity',
      description: 'Only one item',
      timestamp: '2026-06-01',
      type: 'test',
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    resetVirtualMock();
  });

  afterEach(() => {
    cleanup();
  });

  it('list container has tabIndex=0 for keyboard focus', async () => {
    setVirtualItems(5);
    mockFetchUser(
      { id: '1', name: 'Bob', email: 'bob@test.com', avatar: 'b1', bio: 'Dev' },
      mockActivities,
    );
    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });
    await screen.findByText('Bob');

    const listbox = screen.getByRole('listbox', { name: 'Activity history' });
    expect(listbox.getAttribute('tabIndex')).toBe('0');
    expect(listbox).toBeDefined();
  });

  it('handles ArrowDown key to move focus through items', async () => {
    setVirtualItems(5);
    mockFetchUser(
      { id: '1', name: 'Bob', email: 'bob@test.com', avatar: 'b1', bio: 'Dev' },
      mockActivities,
    );
    const user = userEvent.setup();
    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });
    await screen.findByText('Bob');

    const listbox = screen.getByRole('listbox', { name: 'Activity history' });
    await user.click(listbox);
    // activeIndex starts at -1, first ArrowDown moves to 0
    await user.keyboard('{ArrowDown}');
    expect(mockScrollToIndex).toHaveBeenCalledWith(0, { align: 'auto' });

    await user.keyboard('{ArrowDown}');
    expect(mockScrollToIndex).toHaveBeenCalledWith(1, { align: 'auto' });
  });

  it('handles ArrowUp key to move focus to previous item', async () => {
    setVirtualItems(5);
    mockFetchUser(
      { id: '1', name: 'Bob', email: 'bob@test.com', avatar: 'b1', bio: 'Dev' },
      mockActivities,
    );
    const user = userEvent.setup();
    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });
    await screen.findByText('Bob');

    const listbox = screen.getByRole('listbox', { name: 'Activity history' });
    await user.click(listbox);
    await user.keyboard('{ArrowDown}{ArrowDown}'); // Move to index 1
    mockScrollToIndex.mockClear();

    await user.keyboard('{ArrowUp}');
    // ArrowUp from index 1 should go to index 0
    expect(mockScrollToIndex).toHaveBeenCalledWith(0, { align: 'auto' });
  });

  it('handles Home key to jump to first item', async () => {
    setVirtualItems(5);
    mockFetchUser(
      { id: '1', name: 'Bob', email: 'bob@test.com', avatar: 'b1', bio: 'Dev' },
      mockActivities,
    );
    const user = userEvent.setup();
    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });
    await screen.findByText('Bob');

    const listbox = screen.getByRole('listbox', { name: 'Activity history' });
    await user.click(listbox);
    await user.keyboard('{ArrowDown}{ArrowDown}'); // Move to index 2
    mockScrollToIndex.mockClear();

    await user.keyboard('{Home}');
    expect(mockScrollToIndex).toHaveBeenCalledWith(0, { align: 'start' });
  });

  it('handles End key to jump to last item', async () => {
    setVirtualItems(5);
    mockFetchUser(
      { id: '1', name: 'Bob', email: 'bob@test.com', avatar: 'b1', bio: 'Dev' },
      mockActivities,
    );
    const user = userEvent.setup();
    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });
    await screen.findByText('Bob');

    const listbox = screen.getByRole('listbox', { name: 'Activity history' });
    await user.click(listbox);
    mockScrollToIndex.mockClear();

    await user.keyboard('{End}');
    expect(mockScrollToIndex).toHaveBeenCalledWith(4, { align: 'end' });
  });

  it('ArrowUp at first item wraps to last item', async () => {
    setVirtualItems(5);
    mockFetchUser(
      { id: '1', name: 'Bob', email: 'bob@test.com', avatar: 'b1', bio: 'Dev' },
      mockActivities,
    );
    const user = userEvent.setup();
    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });
    await screen.findByText('Bob');

    const listbox = screen.getByRole('listbox', { name: 'Activity history' });
    await user.click(listbox);
    // Home goes to index 0, then ArrowUp wraps to last (index 4)
    await user.keyboard('{Home}');
    mockScrollToIndex.mockClear();

    await user.keyboard('{ArrowUp}');
    expect(mockScrollToIndex).toHaveBeenCalledWith(4, { align: 'auto' });
  });

  it('renders single activity correctly with virtualization', async () => {
    setVirtualItems(1);
    mockFetchUser(
      { id: '1', name: 'Solo', email: 'solo@test.com', avatar: 's1', bio: 'One' },
      singleActivity,
    );
    render(<UserProfile userId="user-1" />, { wrapper: createWrapper() });

    expect(await screen.findByText('Solo')).toBeDefined();
    expect(screen.getByText('Single Activity')).toBeDefined();
    expect(screen.getByText('Activity History (1)')).toBeDefined();
  });
});
