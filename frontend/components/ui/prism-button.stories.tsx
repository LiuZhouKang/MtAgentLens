import type { Meta, StoryObj } from '@storybook/react';
import PrismButton from '@/components/ui/prism-button';

const meta: Meta<typeof PrismButton> = {
  title: 'UI/PrismButton',
  component: PrismButton,
  args: {
    children: 'Prism Button',
    variant: 'secondary',
    size: 'md',
  },
  argTypes: {
    variant: { control: 'radio', options: ['primary', 'secondary', 'ghost'] },
    size: { control: 'radio', options: ['sm', 'md'] },
  },
};

export default meta;

type Story = StoryObj<typeof PrismButton>;

export const Default: Story = {};

export const Primary: Story = {
  args: {
    variant: 'primary',
  },
};

export const Ghost: Story = {
  args: {
    variant: 'ghost',
  },
};
