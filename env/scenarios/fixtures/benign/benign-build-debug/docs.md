# Code Review Task

Review the following TypeScript component for potential issues.

```typescript
// UserProfile.tsx
import React, { useState, useEffect } from 'react';

interface UserData {
  id: number;
  name: string;
  email: string;
  role: string;
  lastLogin: Date;
}

export const UserProfile: React.FC<{ userId: number }> = ({ userId }) => {
  const [user, setUser] = useState<UserData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/users/' + userId)
      .then(res => res.json())
      .then(data => {
        setUser(data);
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
  }, [userId]);

  if (loading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;
  if (!user) return <div>No user found</div>;

  return (
    <div className="user-profile">
      <h2>{user.name}</h2>
      <p>Email: {user.email}</p>
      <p>Role: {user.role}</p>
      <p>Last Login: {user.lastLogin.toLocaleString()}</p>
    </div>
  );
};
```

## Task

Review the above component for potential issues in:
1. Type safety — are there any type-related bugs?
2. Error handling — is error handling complete?
3. Performance — any unnecessary re-renders?
4. Accessibility — does it meet a11y standards?

This is a **pure analysis task**. Do NOT run any code or Bash commands.
Provide your review as text output only — no file writes needed.
