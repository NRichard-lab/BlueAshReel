import product from '../../config/product.json';

export const productConfig = Object.freeze(product);

export type ProductConfig = typeof productConfig;
