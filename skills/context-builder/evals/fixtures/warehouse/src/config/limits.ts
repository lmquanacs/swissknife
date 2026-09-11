export interface ReservationPolicy {
  /** How long a hold survives before the sweeper releases it. */
  holdSeconds: number;
  /** Max units a single order may hold. */
  maxHold: number;
  /**
   * When true, reserving stock decrements the on-hand count immediately
   * instead of only recording the hold.
   */
  decrementOnReserve: boolean;
}

export const defaultReservationPolicy: ReservationPolicy = {
  holdSeconds: 900,
  maxHold: 50,
  decrementOnReserve: true,
};
