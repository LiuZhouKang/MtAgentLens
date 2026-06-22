'use client';

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { ActivityList } from './activity-list';

/* ============================
   UserProfile — Optimized with React Query, virtualization, memoization
   - [Optimization 1] React Query replaces useEffect
   - [Optimization 2] Virtualization for activity list (>100 items)
   - [Optimization 3] Memoized avatar processing
   ============================ */

interface UserProfileProps {
  userId: string;
}

interface User {
  id: string;
  name: string;
  email: string;
  avatar: string;
  bio: string;
}

interface Activity {
  id: string;
  title: string;
  description: string;
  timestamp: string;
  type: string;
}

interface UserProfileData {
  user: User;
  activities: Activity[];
}

/** Error-like objects with a .message property (includes Error, TypeError, etc.) */
interface HasMessage {
  message: string;
}

function hasMessage(value: unknown): value is HasMessage {
  return (
    typeof value === 'object' &&
    value !== null &&
    'message' in value &&
    typeof (value as Record<string, unknown>).message === 'string'
  );
}

/**
 * Safely extract a human-readable message from any error value.
 * Uses a type-guard instead of bare assertions for better type safety.
 */
export function getErrorMessage(error: unknown): string {
  if (hasMessage(error)) return error.message;
  if (typeof error === 'string') return error;
  return 'An unexpected error occurred';
}

/**
 * Process avatar URL with specified dimensions.
 * Pure function — no side effects, no main-thread blocking.
 * Returns empty string for falsy inputs to avoid broken img src.
 */
export function processAvatar(avatar: string, size: number = 200): string {
  const trimmed = avatar.trim();
  if (!trimmed) return '';
  return `https://img.example.com/${trimmed}?w=${size}&q=85&fit=crop`;
}

export function UserProfile({ userId }: UserProfileProps) {
  // Optimization 1: React Query replaces useEffect
  const { data, isLoading, error } = useQuery<UserProfileData>({
    queryKey: ['user-profile', userId],
    queryFn: async () => {
      const res = await fetch(`/api/users/${userId}`);
      if (!res.ok) {
        throw new Error(`Failed to fetch profile for user ${userId}`);
      }
      return res.json();
    },
    staleTime: 5 * 60 * 1000,
  });

  // Optimization 3: Memoize expensive avatar processing
  const processedAvatar = useMemo(() => {
    if (!data?.user?.avatar) return '';
    return processAvatar(data.user.avatar);
  }, [data?.user?.avatar]);

  if (isLoading) {
    return (
      <div className="p-4" role="status" aria-label="Loading user profile">
        <div className="animate-pulse space-y-4">
          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-gray-200" />
            <div className="space-y-2 flex-1">
              <div className="h-6 w-48 bg-gray-200 rounded" />
              <div className="h-4 w-32 bg-gray-200 rounded" />
              <div className="h-3 w-64 bg-gray-200 rounded" />
            </div>
          </div>
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-16 bg-gray-200 rounded" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-red-500" role="alert">
        {getErrorMessage(error)}
      </div>
    );
  }

  if (!data?.user) {
    return (
      <div className="p-4" role="status">
        User not found
      </div>
    );
  }

  const activities = data.activities;

  return (
    <div className="user-profile max-w-2xl mx-auto p-6">
      {/* User Info Section */}
      <div className="user-info flex items-center gap-4 mb-6">
        <img
          src={processedAvatar}
          alt={`${data.user.name}'s avatar`}
          className="w-16 h-16 rounded-full"
        />
        <div>
          <h1 className="text-2xl font-bold">{data.user.name}</h1>
          <p className="text-gray-600">{data.user.email}</p>
          <p className="text-sm text-gray-500">{data.user.bio}</p>
        </div>
      </div>

      {/* Activity List Section */}
      <div className="activities-section">
        <h2 className="text-xl font-semibold mb-4">
          Activity History ({activities.length})
        </h2>
        <ActivityList activities={activities} />
      </div>
    </div>
  );
}
